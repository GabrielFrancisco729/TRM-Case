"""Reproducible, deterministic business logic for Quillon's fictional CSV package.
Source data is READ ONLY. The local SQLite demo stores an append-only action ledger.
"""
from __future__ import annotations
from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np
from pandas.tseries.offsets import BDay

DISPOSITION_CODES = {'No fit / wrong segment','Not interested / no intent','Duplicate / already engaged','Existing customer / CS','Wrong contact / role','Timing / nurture','Bad data / enrichment','Other / needs investigation'}

FILENAMES = [
    'accounts', 'contacts', 'form_submissions', 'mql_events',
    'mql_status_history', 'routing_log', 'notifications_log', 'ad_activity',
    'campaigns', 'opportunities', 'team', 'territory_changes',
    'marketing_ops_changelog', 'weekly_report'
]


def load_data(data_dir: str | Path):
    path = Path(data_dir)
    missing = [x for x in FILENAMES if not (path / f'{x}.csv').exists()]
    if missing:
        raise FileNotFoundError(f'Missing CSV files: {", ".join(missing)}')
    # CRITICAL: default pandas NA parsing silently converts the region "NA"
    # (North America) to NaN. Preserve NA, but treat genuinely empty fields as null.
    datasets = {f: pd.read_csv(path / f'{f}.csv', keep_default_na=False,
                               na_values=[''], dtype={'contact_id': 'string'} if f in ('mql_events','contacts','form_submissions','ad_activity','opportunities') else None)
                for f in FILENAMES}
    for key, column in [('mql_events', 'mql_id'), ('form_submissions', 'submission_id'),
                        ('contacts', 'contact_id'), ('accounts', 'account_id')]:
        if datasets[key][column].duplicated().any():
            raise ValueError(f'Unexpected duplicate primary key: {key}.{column}')
    return datasets


def enriched(d):
    m = d['mql_events'].copy()
    m['mql_timestamp'] = pd.to_datetime(m['mql_timestamp'], utc=True)
    m['quarter'] = np.where(m.mql_timestamp.dt.month <= 6, 'Q2', 'Q3')
    m = m.merge(d['contacts'][['contact_id','first_name','last_name','email','title','seniority','account_id','country']], on='contact_id', how='left', validate='many_to_one')
    m['contact_name'] = m[['first_name','last_name']].fillna('').agg(' '.join,axis=1).str.strip()
    m = m.merge(d['accounts'][['account_id','account_name','region','fit_tier','segment','customer_status','ad_owner']], on='account_id', how='left', validate='many_to_one')
    forms = d['form_submissions'][['submission_id','form_name','campaign_id','product_interest']].rename(columns={'submission_id':'trigger_submission_id'})
    m = m.merge(forms, on='trigger_submission_id', how='left', validate='many_to_one')
    m = m.merge(d['campaigns'][['campaign_id','name']].rename(columns={'name':'campaign_name'}), on='campaign_id', how='left', validate='many_to_one')
    m['form_kind'] = m['trigger_form_id'].fillna('badge_scan').str.replace(r'^frm_', '', regex=True).replace({'demo_v1':'demo','contact_v1':'contact'})
    m['hand_raiser'] = m['form_kind'].isin(['demo','partner'])
    m['sla_deadline'] = m.apply(lambda r: r['mql_timestamp'] + pd.Timedelta(hours=24) if r['hand_raiser'] else r['mql_timestamp'] + BDay(5), axis=1)
    h = d['mql_status_history'].copy()
    h['changed_at'] = pd.to_datetime(h.changed_at, utc=True)
    # Exact source definition: first transition from MQL within allowed window, BY ANYONE.
    moved = h[(h.old_status=='MQL') & (h.new_status!='MQL')].sort_values('changed_at').drop_duplicates('mql_id')
    m = m.merge(moved[['mql_id','changed_at','changed_by','changed_by_user','new_status']], on='mql_id', how='left', validate='one_to_one')
    m['reported_worked'] = m.changed_at.notna() & (m.changed_at <= m.sla_deadline)
    m['seller_status_worked'] = m.reported_worked & m.changed_by.isin(['ad','ae'])
    m['system_late_conversion'] = m.changed_by.eq('lead_routing_tool') & (~m.reported_worked)
    n = d['notifications_log'].copy()
    n['sent_at'] = pd.to_datetime(n.sent_at, utc=True)
    sums = n.groupby('mql_id').agg(notification_count=('notification_id','size'), alert_delivered=('delivered','max'), alert_failed=('delivered',lambda col: (~col).any())).reset_index()
    m = m.merge(sums, on='mql_id', how='left', validate='one_to_one')
    m['alert_delivered'] = m.alert_delivered.astype('boolean').fillna(False).astype(bool)
    m['alert_failed'] = m.alert_failed.astype('boolean').fillna(False).astype(bool)
    m['unassigned_at_creation'] = m.owner_at_mql.isna() | m.owner_at_mql.eq('SYS-QUEUE')
    # An activity record is CONTACT-level, not MQL-level. Match only activity
    # after the MQL and strictly before this contact's next MQL event.
    m = m.sort_values(['contact_id','mql_timestamp','mql_id']).reset_index(drop=True)
    m['next_mql_at'] = m.groupby('contact_id')['mql_timestamp'].shift(-1)
    acts = d['ad_activity'].copy()
    acts['timestamp'] = pd.to_datetime(acts.timestamp,utc=True)
    joined = acts.merge(m[['mql_id','contact_id','mql_timestamp','next_mql_at','sla_deadline']],on='contact_id',how='inner')
    joined = joined[(joined.timestamp >= joined.mql_timestamp) & (joined.next_mql_at.isna() | (joined.timestamp < joined.next_mql_at))]
    activity_summary = joined.groupby('mql_id').agg(logged_actions=('activity_id','count'),first_logged_action=('timestamp','min'),has_seller_activity=('user_id',lambda s:s.str.startswith(('AD-','AE-')).any())).reset_index()
    m = m.merge(activity_summary,on='mql_id',how='left',validate='one_to_one')
    m['logged_actions'] = m.logged_actions.fillna(0).astype(int)
    m['has_seller_activity'] = m['has_seller_activity'].astype('boolean').fillna(False).astype(bool)
    m['activity_in_sla'] = m.first_logged_action.notna() & (m.first_logged_action <= m.sla_deadline)
    m['owner_active_today'] = m.current_owner.map(d['team'].set_index('user_id').active.to_dict())
    # Auditable heuristic, not an ML score: does not claim conversion probability.
    fit_score = m.fit_tier.map({'A':25,'B':15,'C':5,'D':0}).fillna(0)
    form_score = m.form_kind.map({'demo':55,'partner':50,'contact':42,'gated_beacon_brief':14,'gated_sanctions_report':12,'gated_buyers_guide':12,'webinar_reg':9,'newsletter':2,'badge_scan':0}).fillna(0)
    m['queue_score'] = (fit_score + form_score + 10*m.priority.eq('P1')).astype(int)
    m['data_warning'] = np.select([m.unassigned_at_creation, m.alert_failed, m.account_id.isna()],
                                     ['No AD assigned','Failed alert','Unmatched account'], default='')
    return m.sort_values('mql_timestamp',ascending=False).reset_index(drop=True)


def volume_waterfall(d, m):
    form = d['form_submissions'].copy()
    form['timestamp'] = pd.to_datetime(form.timestamp,utc=True)
    form['quarter'] = np.where(form.timestamp.dt.month<=6,'Q2','Q3')
    q2_forms = int((form.quarter=='Q2').sum())
    q3_forms = int((form.quarter=='Q3').sum())
    q2_imports = int(((m.quarter=='Q2')&(m.source=='list_import')).sum())
    q3_imports = int(((m.quarter=='Q3')&(m.source=='list_import')).sum())
    matching_ids = set(m.trigger_submission_id.dropna().astype(str))
    missed = form[~form.submission_id.isin(matching_ids)].copy()
    missing_q2 = int((missed.quarter=='Q2').sum())
    missing_q3 = int((missed.quarter=='Q3').sum())
    missing_v2 = int(((missed.quarter=='Q3')&missed.form_id.isin(['frm_demo_v2','frm_contact_v2'])).sum())
    q2_mql = int((m.quarter=='Q2').sum())
    q3_mql = int((m.quarter=='Q3').sum())
    assert q2_forms + q2_imports - missing_q2 == q2_mql
    assert q3_forms + q3_imports - missing_q3 == q3_mql
    return dict(q2_mql=q2_mql,q3_mql=q3_mql,q2_forms=q2_forms,q3_forms=q3_forms,
                q2_imports=q2_imports,q3_imports=q3_imports,missing_q2=missing_q2,
                missing_q3=missing_q3,missing_v2=missing_v2,
                other_unmatched=missing_q3-missing_v2,
                missing_submissions=missed)


def not_worked_breakdown(m, quarter='Q3'):
    df = m[m.quarter==quarter].copy()
    # Mutually exclusive bookkeeping buckets; not a causal attribution.
    df['bucket'] = np.select([
        df.reported_worked,
        df.unassigned_at_creation,
        df.alert_failed,
        df.has_seller_activity,
    ],['Worked on time','System queue / unassigned','Alert delivery failed','Activity logged, no on-time status change'],default='Assigned & alerted, no activity logged')
    return df.groupby('bucket').size().sort_values(ascending=False).rename('mqls').reset_index()


def init_db(db_path):
    path = Path(db_path); path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        mql_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        action_type TEXT NOT NULL,
        new_status TEXT,
        assigned_to TEXT,
        channel TEXT,
        note TEXT NOT NULL,
        reason_code TEXT
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS actions_mql_id ON actions(mql_id)')
        if 'reason_code' not in {row[1] for row in conn.execute('PRAGMA table_info(actions)')}:
            conn.execute('ALTER TABLE actions ADD COLUMN reason_code TEXT')


def get_actions(db_path):
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query('SELECT id,occurred_at,mql_id,actor,action_type,new_status,assigned_to,channel,note,reason_code FROM actions ORDER BY id DESC', conn)


def add_action(db_path, mql_id, actor, action_type, note, valid_mql_ids, new_status=None, assigned_to=None, channel=None, valid_users=None, reason_code=None, valid_reason_codes=None):
    if mql_id not in valid_mql_ids:
        raise ValueError('Unknown MQL ID')
    if not actor or actor.strip()=='' or len(actor)>100:
        raise ValueError('An actor is required')
    choices = {'Log outreach','Change status','Reassign','Flag data issue','Return to marketing','Return to AD'}
    if action_type not in choices: raise ValueError('Invalid action type')
    if action_type == 'Change status' and new_status not in {'Working','Accepted','Disqualified','Converted','MQL'}:
        raise ValueError('Select a permitted status')
    if action_type == 'Change status' and new_status == 'Disqualified' and not note.strip():
        raise ValueError('A disqualification reason is mandatory')
    if (action_type == 'Change status' and new_status == 'Disqualified') or action_type == 'Return to marketing':
        if reason_code not in (valid_reason_codes if valid_reason_codes is not None else DISPOSITION_CODES):
            raise ValueError('Select a valid structured reason code')
    if action_type == 'Return to marketing' and not note.strip():
        raise ValueError('Explain the requested Marketing review')
    if action_type == 'Return to AD' and not note.strip():
        raise ValueError('Explain why Marketing returned the lead to the AD')
    if action_type == 'Flag data issue' and not note.strip():
        raise ValueError('Describe the data issue in notes')
    if action_type == 'Reassign' and (not assigned_to or (valid_users is not None and assigned_to not in valid_users)):
        raise ValueError('Select an active AD')
    if action_type == 'Log outreach' and channel not in {'Email','Call','Meeting','Task'}:
        raise ValueError('Select a channel')
    if len(note)>3000: raise ValueError('Note is too long')
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        status = new_status if action_type=='Change status' else ('Marketing Review' if action_type=='Return to marketing' else ('MQL' if action_type=='Return to AD' else None))
        cursor = conn.execute('''INSERT INTO actions (mql_id, actor, action_type, new_status, assigned_to, channel, note, reason_code)
                        VALUES (?,?,?,?,?,?,?,?)''', (mql_id,actor,action_type,status,
                              assigned_to if action_type=='Reassign' else None,
                              channel if action_type=='Log outreach' else None, note.strip(), reason_code))
        return cursor.lastrowid


def overlay_actions(df, actions):
    result = df.copy()
    result['effective_status'] = result.status_today
    result['effective_owner'] = result.current_owner
    result['demo_action_count'] = 0
    result['last_demo_action_type'] = None
    if not actions.empty:
        counts = actions.groupby('mql_id').size()
        result['demo_action_count'] = result.mql_id.map(counts).fillna(0).astype(int)
        ordered = actions.sort_values('id')
        latest = ordered.drop_duplicates('mql_id', keep='last')
        result['last_demo_action_type'] = result.mql_id.map(latest.set_index('mql_id').action_type)
        owner = ordered[(ordered.action_type=='Reassign') & ordered.assigned_to.notna()].drop_duplicates('mql_id',keep='last')
        status = ordered[ordered.action_type.isin(['Change status','Return to marketing','Return to AD']) & ordered.new_status.notna()].drop_duplicates('mql_id',keep='last')
        result['effective_status'] = result.mql_id.map(status.set_index('mql_id').new_status).fillna(result.status_today)
        result['effective_owner'] = result.mql_id.map(owner.set_index('mql_id').assigned_to).fillna(result.current_owner)
    # A handoff is not a seller response. A transferred MQL must appear in the
    # new owner's default queue until they themselves take an action.
    result['needs_ad_action'] = (result.effective_status.eq('MQL') &
        ((result.demo_action_count==0) | result.last_demo_action_type.isin(['Reassign','Return to AD'])))
    return result
