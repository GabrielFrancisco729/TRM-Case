"""Additional evidence-driven analysis and reversible, human-controlled qualification.

No LLM is allowed to change CRM status, owner, eligibility, or routing. All
historical metrics are calculated from the supplied CSVs; demo actions live in
SQLite and do not rewrite the observed history.
"""
from __future__ import annotations
from datetime import date
import hashlib
import json
import sqlite3
from pathlib import Path
import pandas as pd
import numpy as np

DEFAULT_WEIGHTS = {
    'demo': 55, 'partner': 50, 'contact': 42,
    'fit_a': 25, 'fit_b': 15, 'fit_c': 5,
    'priority_p1': 10, 'seniority_clevel': 12,
    'seniority_vp': 10, 'seniority_director': 6,
    'validated_open_opp': 8, 'beacon_interest': 6,
    'personal_email': -12, 'unmatched_account': -8,
    'recent_30d': 5, 'stale_days':45,
}
BOUNDS = {key:((-30, 0) if val < 0 else (0, 75)) for key,val in DEFAULT_WEIGHTS.items()}
BOUNDS['stale_days']=(7,120)
FREEMAIL = {'gmail.com','yahoo.com','hotmail.com','outlook.com','live.com','icloud.com','aol.com','proton.me','protonmail.com'}
VALID_DECISIONS = {'Send to AD','Send to CS','Hold for enrichment','Return to nurture'}
DEFAULT_REJECTION_REASONS = ['No fit / wrong segment','Not interested / no intent','Duplicate / already engaged','Existing customer / CS','Wrong contact / role','Timing / nurture','Bad data / enrichment','Other / needs investigation']

def setup(conn: sqlite3.Connection):
    conn.execute('''CREATE TABLE IF NOT EXISTS scoring_config (
      id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      actor TEXT NOT NULL, weights_json TEXT NOT NULL, reason TEXT NOT NULL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS qualification_reviews (
      id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      mql_id TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL,
      reason TEXT NOT NULL, suggestion_json TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_qualification_mql ON qualification_reviews(mql_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS reason_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        actor TEXT NOT NULL, reasons_json TEXT NOT NULL, reason TEXT NOT NULL)''')
    conn.commit()

def config(conn):
    setup(conn)
    row=conn.execute('SELECT weights_json FROM scoring_config ORDER BY id DESC LIMIT 1').fetchone()
    weights=DEFAULT_WEIGHTS.copy()
    if row: weights.update(json.loads(row[0]))
    return weights

def set_config(conn, values, reason, actor='MANAGER-DEMO'):
    if set(values)!=set(DEFAULT_WEIGHTS):
        raise ValueError('All scoring parameters must be provided, with no extra parameters')
    for k, v in values.items():
        if isinstance(v,bool) or not isinstance(v,int) or not BOUNDS[k][0] <= v <= BOUNDS[k][1]:
            raise ValueError(f'Invalid weight for {k}: allowed {BOUNDS[k]}')
    if not reason.strip(): raise ValueError('Reason for a scoring change is required')
    setup(conn)
    conn.execute('INSERT INTO scoring_config(actor,weights_json,reason) VALUES (?,?,?)',
                 (actor,json.dumps(values,sort_keys=True),reason.strip()[:3000]))
    conn.commit()

def undo_config(conn, actor='MANAGER-DEMO'):
    setup(conn)
    rows=conn.execute('SELECT weights_json FROM scoring_config ORDER BY id DESC LIMIT 2').fetchall()
    old=json.loads(rows[1][0]) if len(rows)>1 else DEFAULT_WEIGHTS.copy()
    set_config(conn,old,'Undo: revert to previous approved weights',actor)

def get_rejection_reasons(conn):
    setup(conn)
    row=conn.execute('SELECT reasons_json FROM reason_config ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row[0]) if row else DEFAULT_REJECTION_REASONS.copy()

def set_rejection_reasons(conn, codes, justification, actor='MANAGER-DEMO'):
    if not isinstance(codes,list) or not 2 <= len(codes) <= 18:
        raise ValueError('Provide 2 to 18 disposition categories')
    cleaned=[x.strip() for x in codes if isinstance(x,str)]
    if len(cleaned)!=len(codes) or any(not 4 <= len(x) <= 80 for x in cleaned):
        raise ValueError('Each category needs 4–80 characters')
    if len({x.casefold() for x in cleaned})!=len(cleaned):
        raise ValueError('Duplicate disposition categories')
    if not isinstance(justification,str) or len(justification.strip())<5:
        raise ValueError('A justification is required')
    setup(conn)
    conn.execute('INSERT INTO reason_config(actor,reasons_json,reason) VALUES (?,?,?)',
                 (actor,json.dumps(cleaned),justification.strip()[:1000]))
    conn.commit()

def undo_rejection_reasons(conn, actor='MANAGER-DEMO'):
    setup(conn)
    last=conn.execute('SELECT reasons_json FROM reason_config ORDER BY id DESC LIMIT 2').fetchall()
    restored=json.loads(last[1][0]) if len(last)>1 else DEFAULT_REJECTION_REASONS.copy()
    set_rejection_reasons(conn,restored,'Undo: restore previous reason categories',actor)
    return restored

def acquisition_source_evidence(d, m):
    # Observed channel volume, no causal attribution.
    forms=d['form_submissions'].copy()
    forms['month']=pd.to_datetime(forms.timestamp,utc=True).dt.month
    forms['quarter']=np.where(forms.month<=6,'Q2','Q3')
    counts=forms.groupby(['quarter','source']).size()
    organic_q2=int(counts.get(('Q2','organic_search'),0))
    organic_q3=int(counts.get(('Q3','organic_search'),0))
    social={name:int((forms[(forms.month==month)&forms.source.eq('paid_social')]).shape[0])
            for name,month in [('Jul',7),('Aug',8),('Sep',9)]}
    organic_combined={quarter:int(forms[(forms.quarter==quarter) & forms.source.isin(['organic_search','direct'])].shape[0]) for quarter in ('Q2','Q3')}
    q2_form_mql=int(((m.quarter=='Q2') & m.source.ne('list_import')).sum())
    q3_form_mql=int(((m.quarter=='Q3') & m.source.ne('list_import')).sum())
    q2_mql=int((m.quarter=='Q2').sum())
    q3_mql=int((m.quarter=='Q3').sum())
    q2_forms=int((forms.quarter=='Q2').sum())
    q3_forms=int((forms.quarter=='Q3').sum())
    total_mql_drop=q2_mql-q3_mql
    form_volume_drop=q2_forms-q3_forms
    other_bridge_effects=total_mql_drop-form_volume_drop
    assert total_mql_drop>0 and 0<=form_volume_drop<=total_mql_drop, 'Unexpected historical reconciliation'
    return {'q2_form_mql':q2_form_mql,'q3_form_mql':q3_form_mql,
            'form_mql_decline_pct':round(100*(q2_form_mql-q3_form_mql)/q2_form_mql,1),
            'total_mql_drop':total_mql_drop,
            'form_volume_drop':form_volume_drop,
            'form_volume_share_pct':round(100*form_volume_drop/total_mql_drop,1),
            'other_bridge_effects':other_bridge_effects,
            'other_bridge_share_pct':round(100*other_bridge_effects/total_mql_drop,1),
            'organic_search_forms':{'Q2':organic_q2,'Q3':organic_q3},
            'direct_plus_organic_forms':organic_combined,
            'paid_social_monthly_submissions':social,
            'paid_social_q2':int(counts.get(('Q2','paid_social'),0)),
            'paid_social_q3':int(counts.get(('Q3','paid_social'),0)),
            'change_log_note':'Marketing ops changelog reports paid social budget reallocation on August 3, 2026.',
            'interpretation':'Of 173 fewer MQL events, the net reduction of 16 raw form submissions explains 9.2% of the arithmetic bridge; the remaining 157 (90.8%) reflect the one-off badge import and unmatched form-to-MQL transitions. Excluding the one-off import, form-generated MQLs declined 17.1% (563 to 467). Paid-social monthly trends are supporting channel context, not the definition of the 9%.'}

def evidence_score(m, d, weights=None):
    """Recompute transparent queue points + trustworthy evidence; never train on biased worked labels.

    Uses opportunity only if the opportunity's contact is matched to its account,
    and close_date is after the source extract. Returns a full explanation.
    """
    w=weights or DEFAULT_WEIGHTS
    rows=m.copy(); contacts=d['contacts'].set_index('contact_id'); opp=d['opportunities'].copy()
    opp['close_date']=pd.to_datetime(opp.close_date,errors='coerce')
    clean=opp[(opp.close_date>=pd.Timestamp('2026-10-08')) & (opp.type!='Renewal')].copy()
    account_of_contact=contacts.account_id.to_dict()
    clean=clean[clean.apply(lambda r: pd.notna(r.contact_id) and account_of_contact.get(r.contact_id)==r.account_id,axis=1)]
    valid_accounts=set(clean.account_id.dropna())
    opp_mismatched=opp[opp.apply(lambda r: pd.notna(r.contact_id) and account_of_contact.get(r.contact_id)!=r.account_id,axis=1)]
    opp_stale=opp[opp.close_date<pd.Timestamp('2026-10-08')]
    stale_accounts=set(opp_stale.account_id)
    mismatched_accounts=set(opp_mismatched.account_id)
    parts=[];trust=[]; quals=[];scores=[];lanes=[]
    for r in rows.itertuples(index=False):
        x=[]; flags=[]
        def add(label, pts):
            if pts:x.append({'signal':label,'points':int(pts)})
        form=str(r.form_kind or '')
        if form in ['demo','partner','contact']: add('High-intent form: '+form,w[form])
        elif form.startswith('gated_'): add('Gated content',12)
        elif form=='webinar_reg':add('Webinar registration',9)
        elif form=='newsletter':add('Newsletter',2)
        fit=str(r.fit_tier) if pd.notna(r.fit_tier) else ''
        if fit in ['A','B','C']:add('Account fit '+fit,w['fit_'+fit.lower()])
        if r.priority=='P1':add('P1 priority',w['priority_p1'])
        seniority=str(r.seniority).lower() if pd.notna(r.seniority) else ''
        seniority_key={'c-level':'seniority_clevel','vp':'seniority_vp','director':'seniority_director'}.get(seniority)
        if seniority_key:add('Contact seniority: '+seniority,w[seniority_key])
        if pd.notna(r.account_id) and r.account_id in valid_accounts: add('Verified active opportunity on account',w['validated_open_opp'])
        elif pd.notna(r.account_id) and r.account_id in stale_accounts:flags.append('Stale opportunity close date: not scored')
        elif pd.notna(r.account_id) and r.account_id in mismatched_accounts:flags.append('Opportunity contact/account mismatch: not scored')
        if str(r.product_interest).strip().lower()=='beacon' if pd.notna(r.product_interest) else False:add('Beacon interest',w['beacon_interest'])
        mail=str(r.email).lower() if pd.notna(r.email) else ''
        if '@' in mail and mail.rsplit('@',1)[-1] in FREEMAIL:add('Personal email domain',w['personal_email']);flags.append('Personal email (not proof of low fit)')
        if pd.isna(r.account_id):add('No account match',w['unmatched_account']);flags.append('CRM account missing: requires enrichment')
        if pd.isna(r.fit_tier):flags.append('No account fit score')
        if pd.isna(r.title): flags.append('Missing title')
        if r.customer_status=='Customer':flags.append('Existing customer: coordinate with CS')
        if r.source=='list_import':flags.append('Imported badge: no explicit hand-raise')
        age=999
        try:
            age=(pd.Timestamp('2026-10-08',tz='UTC') - r.mql_timestamp).days
            if 0<=age<=30:add('Recent within 30 days of extract',w['recent_30d'])
        except Exception:pass
        score=max(0,sum(z['points'] for z in x))
        if form in {'demo','partner','contact'}: lane='Hand-raisers'
        elif r.customer_status=='Customer': lane='CS review'
        elif 0 <= age and age>w['stale_days']:lane='Aged / recycle review'
        elif score>=40:lane='Qualified fit'
        else:lane='Nurture / low signal'
        lanes.append(lane)
        scores.append(score); parts.append(x);trust.append(flags)
        quals.append(qualification_suggestion_row(r))
    rows['queue_score']=scores;rows['score_explanation']=parts;rows['trust_flags']=trust
    rows['qualification_suggestion']=quals
    rows['queue_lane']=lanes
    return rows

def control_group(mql_id):
    # Deterministic selection stable across runs and Python processes.
    return int(hashlib.sha256(str(mql_id).encode()).hexdigest()[:8],16)%5==0

def qualification_suggestion_row(r):
    # Never auto-disqualify. We cannot observe intent or vendor relationship
    # for unlinked leads from this package; even a disqualification label may
    # be biased by which sellers actually received notifications.
    if r.form_kind in {'demo','partner','contact'}:
        return {'decision':'Send to AD','basis':'High-intent hand-raise; respond inside its SLA','confidence':'Rule / deterministic','needs_review':False}
    if r.customer_status=='Customer':
        return {'decision':'Send to CS','basis':'CRM says existing customer; human should verify expansion/active-opportunity context','confidence':'Rule / account match','needs_review':True}
    if pd.isna(r.account_id):
        return {'decision':'Hold for enrichment','basis':'No matched CRM account. Need firmographic validation; do not reject automatically','confidence':'Rule / missing evidence','needs_review':True}
    return {'decision':'Send to AD','basis':'Account matched. Non-hand-raiser: low intent is not sufficient to auto-reject','confidence':'Rule / deterministic','needs_review':False}

def qualification_list(m, conn):
    setup(conn)
    last={r[0]:{'decision':r[1],'reason':r[2],'actor':r[3],'created_at':r[4]} for r in conn.execute('''
      SELECT mql_id,decision,reason,actor,created_at FROM qualification_reviews
      WHERE id IN (SELECT MAX(id) FROM qualification_reviews GROUP BY mql_id)''').fetchall()}
    rows=[]
    for r in m.itertuples(index=False):
        suggested=r.qualification_suggestion
        decision=last.get(r.mql_id)
        if suggested['needs_review'] or decision:
            rows.append({'mql_id':r.mql_id,'contact_name':r.contact_name,'account_name':r.account_name if pd.notna(r.account_name) else None,
                         'form_kind':r.form_kind,'region':r.region if pd.notna(r.region) else None,
                         'current_owner':r.current_owner,'customer_status':r.customer_status if pd.notna(r.customer_status) else None,
                         'title':r.title if pd.notna(r.title) else None,'email':r.email,
                         'control':control_group(r.mql_id),'suggestion':suggested,'review':decision})
    return rows

def record_review(conn, mql_id, actor, decision, reason, suggested):
    if decision not in VALID_DECISIONS:raise ValueError('Invalid qualification decision')
    if not reason or not reason.strip():raise ValueError('A human justification is required')
    setup(conn)
    conn.execute('INSERT INTO qualification_reviews(mql_id,actor,decision,reason,suggestion_json) VALUES (?,?,?,?,?)',
                 (mql_id,actor,decision,reason.strip()[:3000],json.dumps(suggested)))
    conn.commit()

def operating_breakdown(d,m, quarter='Q3'):
    """Mutually exclusive evidence labels, strictly first-match. Does not infer causation.

    Historical 'reported worked' remains exactly the CSV business definition.
    Claims about real contact require an activity in the matching MQL time window.
    """
    df=m[m.quarter==quarter].copy(); counts={}; labels=[]
    for r in df.itertuples(index=False):
        status=str(r.changed_by) if pd.notna(r.changed_by) else ''
        # Today's inactivity != proof the AD was inactive at the time of the original MQL.
        if r.reported_worked:cat='Reported worked (status transition in SLA)'
        elif bool(r.activity_in_sla and r.has_seller_activity):cat='Seller activity in SLA, but status not updated'
        elif r.customer_status=='Customer':cat='Existing customer, AD/CS handoff to review'
        elif r.unassigned_at_creation or r.current_owner=='SYS-QUEUE':cat='Unassigned at creation / system queue'
        elif not r.alert_delivered:cat='No verified delivered notification'
        elif r.owner_active_today is False or r.owner_active_today==False:cat='Owner inactive at data extract'
        elif r.system_late_conversion:cat='System status changed after SLA'
        else:cat='Assigned / delivered, no recorded action in SLA'
        labels.append(cat)
    df['operational_bucket']=labels
    order=['Reported worked (status transition in SLA)','Seller activity in SLA, but status not updated',
           'Existing customer, AD/CS handoff to review','Unassigned at creation / system queue',
           'No verified delivered notification','Owner inactive at data extract',
           'System status changed after SLA','Assigned / delivered, no recorded action in SLA']
    result=[{'bucket':cat,'mqls':int((df.operational_bucket==cat).sum())} for cat in order]
    # Strictly defined denominators: not an inferred true adoption rate.
    no_issue=df[~df.operational_bucket.isin(order[2:7])]
    return {'rows':result,'total':len(df),'reported_worked':int(df.reported_worked.sum()),
            'seller_activity_in_sla':int((df.activity_in_sla & df.has_seller_activity).sum()),
            'delivery_ownership_risk_buckets':int(df.operational_bucket.isin(order[3:6]).sum()),
            'adjusted_adoption_numerator':int(df.operational_bucket.isin([order[0],order[1]]).sum()),
            'adjusted_adoption_denominator':int(len(df)-df.operational_bucket.isin(order[3:6]).sum()),
            'comparable_proxy_numerator':int(df.operational_bucket.isin([order[0],order[1]]).sum()),
            'comparable_proxy_denominator':int(df.operational_bucket.isin([order[0],order[1],order[7]]).sum()),
            'seller_status_worked':int(df.seller_status_worked.sum()),
            'customer_mqls':int((df.customer_status=='Customer').sum()),
            'owner_inactive_at_extract':int((df.owner_active_today==False).sum()),
            'unknown_account':int(df.account_id.isna().sum()),
            'no_sale_action_at_all':int((~df.has_seller_activity).sum()),
            'method':'First-match evidence categories; not a causal decomposition or verified seller contact rate.'}

def risk_checks(d,m):
    forms=d['form_submissions'].copy()
    q3=forms[pd.to_datetime(forms.timestamp,utc=True).dt.quarter==3]
    linked=set(m.trigger_submission_id.dropna().astype(str))
    unlinked=q3[~q3.submission_id.isin(linked)]
    demo=unlinked[unlinked.form_id.isin(['frm_demo_v2','frm_contact_v2'])]
    opp=d['opportunities'].copy(); contact_map=d['contacts'].set_index('contact_id').account_id.to_dict()
    wrong=opp[opp.apply(lambda r:pd.notna(r.contact_id) and contact_map.get(r.contact_id)!=r.account_id,axis=1)]
    stale=opp[pd.to_datetime(opp.close_date,errors='coerce')<pd.Timestamp('2026-10-08')]
    badprod=forms[forms.product_interest.notna() & ~forms.product_interest.isin(['Beacon','Sentinel'])]
    q3m=m[m.quarter=='Q3']
    h=d['mql_status_history']
    automated=h[(h.mql_id.isin(q3m.mql_id)) & h.changed_by.eq('lead_routing_tool') & h.new_status.eq('Converted')]
    frequent=q3m.groupby('contact_id').size()
    frequent_contacts=int((frequent>=3).sum())
    repeat_events=int((frequent-1).clip(lower=0).sum())
    return [
      {'id':'form-v2','severity':'critical','count':len(demo),'title':'New CMS high-intent forms without matching MQL','evidence':'Cross-check form submission ID against MQL trigger submission ID. CMS migrated Aug 4.','action':'Review new trigger IDs, exclusions and deduplication in source platform; do not fabricate MQL rows.'},
      {'id':'unlinked','severity':'warning','count':len(unlinked)-len(demo),'title':'Other Q3 forms without matching MQL','evidence':'Remaining unmatched form submissions. Some may be intentional suppressions.','action':'Audit rule changes and suppression logs before recovering any MQL.'},
      {'id':'unassigned','severity':'critical','count':int(q3m.unassigned_at_creation.sum()),'title':'Q3 MQLs initially assigned to a system queue','evidence':'owner_at_mql empty or SYS-QUEUE.','action':'Route to an active AD after verifying territory.'},
      {'id':'alert','severity':'serious','count':int((~q3m.alert_delivered).sum()),'title':'No verified delivered notification in Q3','evidence':'No successful delivery recorded; does not prove the AD never learned about the lead.','action':'Investigate channel history, retries and escalation.'},
      {'id':'inactive','severity':'serious','count':int((q3m.owner_active_today==False).sum()),'title':'Q3 leads with owner inactive at extract','evidence':'Team roster as of data pull; requires effective-dated roster to attribute a failure.','action':'Audit effective-dated territory and reroute if still owned by inactive users.'},
      {'id':'stale-opp','severity':'warning','count':len(stale),'title':'Opportunity dates stale at extraction','evidence':'Opportunity close date predates 2026-10-08; stage may not have been synchronized.','action':'Validate opportunity status with CRM owner; do not score as confirmed open.'},
      {'id':'cross-account','severity':'critical','count':len(wrong),'title':'Opportunity primary contact does not match account','evidence':'Opportunity.contact_id → contacts.account_id differs from opportunity.account_id.','action':'Human CRM audit before associating opportunity with an MQL.'},
      {'id':'product','severity':'warning','count':len(badprod),'title':'Product interest not in the expected registry','evidence':'Source contains product_interest values other than Sentinel/Beacon.','action':'Flag and request schema correction; do not let an LLM silently overwrite.'},
      {'id':'auto-convert','severity':'warning','count':int(automated.mql_id.nunique()),'title':'Automated conversions without proven seller outreach','evidence':'Q3 status history: lead_routing_tool changed status to Converted. The status alone is not proof of outreach.','action':'Reconcile conversion triggers, downstream opportunities and verified seller actions.'},
      {'id':'repeat-mql','severity':'warning','count':frequent_contacts,'title':'Contacts with three or more MQL events in Q3','evidence':f'{repeat_events} additional MQL events beyond each contact first event (17 contacts repeat overall in Q3). Distinct MQL events remain legitimate source rows.','action':'Audit event re-entry and attribution windows; preserve event-level history without blanket deduplication.'},
    ]

def team_comparison(d,m,quarter='Q3'):
    """Descriptive, mix-adjusted seller-action proxy by AD region.

    *Excludes* delivery-defective and customer MQLs; uses logged seller activity
    or seller status change as a proxy, not independently verified outreach.
    Estimates are descriptive standardized expectations, not treatment effects.
    """
    df=m[m.quarter==quarter].copy()
    team=d['team'].set_index('user_id')
    owner_to_region=team.region.to_dict()
    owner_to_end=team.end_date.to_dict()
    df['owner_team_region']=df.owner_at_mql.map(owner_to_region)
    df['owner_end']=pd.to_datetime(df.owner_at_mql.map(owner_to_end),errors='coerce',utc=True)
    df['owner_inactive_when_mql']=df.owner_end.notna() & (df.mql_timestamp>=df.owner_end)
    df['delivered_to_live_ad']=(df.owner_at_mql.notna() & df.owner_at_mql.ne('SYS-QUEUE') &
                                df.alert_delivered & (~df.owner_inactive_when_mql) &
                                df.owner_team_region.notna())
    df['eligible_comparison']=df.delivered_to_live_ad & df.customer_status.ne('Customer').fillna(True)
    valid=df[df.eligible_comparison].copy()
    if valid.empty:return []
    valid['seller_evidence']=valid.seller_status_worked | (valid.activity_in_sla & valid.has_seller_activity)
    valid['mix']=np.where(valid.form_kind.isin(['demo','partner','contact']),'hand','content')+'|'+np.where(valid.fit_tier.isin(['A','B']),'AB','other')
    p=valid.groupby('mix').seller_evidence.mean().to_dict()
    out=[]
    for rg,group in df.groupby('owner_team_region',dropna=False):
        if pd.isna(rg):continue
        delivered=valid[valid.owner_team_region==rg]
        n=len(delivered);observed=int(delivered.seller_evidence.sum())
        expected=float(delivered.mix.map(p).sum()) if n else 0
        out.append({'region':rg,'routed':len(group),
                    'not_comparable':len(group)-n,'comparable':n,
                    'action_proxy':observed,'rate_pct':round(100*observed/n,1) if n else 0,
                    'mix_expected_actions':round(expected,1),
                    'actual_vs_expected':round(observed/expected,2) if expected else None,
                    'hand_raiser_share_pct':round(100*group.form_kind.isin(['demo','partner','contact']).mean(),1),
                    'small_sample':n<30})
    return sorted(out,key=lambda x:x['routed'],reverse=True)

def qualification_backtest(d,m,quarter='Q3'):
    """Selective-label audit: observed seller decisions only (not true fit labels)."""
    h=d['mql_status_history']
    human=h[(h.old_status=='MQL') & h.changed_by.isin(['ad','ae'])].copy()
    first=human.sort_values('changed_at').drop_duplicates('mql_id')
    q=m[m.quarter==quarter].merge(first[['mql_id','new_status','note']],on='mql_id',how='inner',suffixes=('','_seller'))
    if q.empty:return {'total_decided':0,'total_disqualified':0,'backtest':[]}
    labels=[]
    rules=[
        ('Content / webinar / newsletter', lambda x:~x.form_kind.isin(['demo','partner','contact'])),
        ('Newsletter',lambda x:x.form_kind.eq('newsletter')),
        ('Low or missing account fit (C / D / blank)',lambda x:x.fit_tier.isna() | x.fit_tier.isin(['C','D'])),
        ('Individual contributor seniority',lambda x:x.seniority.eq('IC')),
        ('Missing CRM account',lambda x:x.account_id.isna()),
        ('Existing customer',lambda x:x.customer_status.eq('Customer')),
    ]
    for title,fn in rules:
        group=q[fn(q).fillna(False)]
        n=len(group); rejects=int(group.new_status_seller.eq('Disqualified').sum())
        labels.append({'condition':title,'decided':n,'rejected':rejects,
                       'rejection_rate':round(100*rejects/n,1) if n else None})
    return {'total_decided':len(q),'total_disqualified':int(q.new_status_seller.eq('Disqualified').sum()),
            'backtest':labels,'caveat':'Observed seller decisions are selection-biased and do not establish whether a lead was actually qualified. Rules must not auto-reject.'}
