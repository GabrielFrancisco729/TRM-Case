"""Evidence-backed AD metrics, lead journey, and safe next-action suggestions.

No new event sources are invented and no suggested action is executed here.
"""
from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd

HAND_RAISERS={'demo','contact','partner'}

def _clean(val):
    return None if val is None or (not isinstance(val,(dict,list)) and pd.isna(val)) else val

def _utc(value):
    if value is None or pd.isna(value):return None
    return pd.to_datetime(value,utc=True)

def next_action_rule(lead:dict, account:dict|None=None, activities:list|None=None):
    """Explainable suggestion; never uses a model or commits seller activity."""
    account=account or {}
    status=str(lead.get('effective_status') or '')
    disposition=lead.get('qualification_approved')
    if status in {'Marketing Review','Disqualified','Converted'} or disposition in {'Send to CS','Hold for enrichment','Return to nurture'}:
        return {'source':'explainable_rule','title':'Review existing disposition before taking action',
                'action_type':None,'channel':None,
                'rationale':'This lead is already closed or under Marketing Ops disposition. No additional AD action is suggested.',
                'confidence':'rule_based','requires_human_approval':True,'blocked':True,
                'email_subject':None,'email_draft':None,
                'notes':'Consult the manager and historical context before considering a new action.'}
    kind=str(_clean(lead.get('form_kind')) or '').lower()
    customer=str(_clean(account.get('customer_status')) or '').lower()
    has_email=bool(_clean(lead.get('email')))
    rationale=[]
    if customer in {'customer','existing customer','active customer'}:
        action='Return to marketing';channel=None
        title='Verify customer ownership with Marketing / CS'
        rationale=['CRM account is an existing customer. Confirm an expansion/CS handoff before initiating new-business outreach.']
    elif kind in HAND_RAISERS:
        action='Log outreach';channel='Email' if has_email else 'Call'
        title='Respond to this high-intent request'
        rationale=[f'{kind.title()} form signals explicit interest. Respond within the applicable response SLA.']
    elif not _clean(lead.get('account_id')):
        action='Flag data issue';channel=None
        title='Verify account match and contact context'
        rationale=['No matched CRM account. Enrichment/validation should precede a personalized outreach.']
    elif not has_email:
        action='Flag data issue';channel=None
        title='Enrich contact details before outreach'
        rationale=['No email address available in the supplied contact record.']
    else:
        action='Log outreach';channel='Email'
        title='Review context and prepare personalized outreach'
        rationale=['Contact has an identifiable account and is eligible for a human-reviewed follow-up.']
    if lead.get('trust_flags'):
        rationale.append('Verify the visible data-quality warnings before relying on account context.')
    return {'source':'explainable_rule','title':title,'action_type':action,'channel':channel,
            'rationale':' '.join(rationale),'confidence':'rule_based',
            'requires_human_approval':True,'email_subject':None,'email_draft':None,
            'notes':'No message has been sent and no CRM record has been changed.'}

def journey_for_lead(lead:dict, related:dict):
    def phase(code,label,state,evidence):return dict(code=code,label=label,state=state,evidence=evidence)
    form=related.get('submission')
    assigned=lead.get('effective_owner') not in (None,'','SYS-QUEUE')
    notif=related.get('notifications') or []
    delivered=any(str(n.get('delivered')).lower()=='true' for n in notif)
    local=related.get('local_actions') or []
    activities=related.get('activities') or []
    activity=any(a.get('action_type') in ('Log outreach','Change status') for a in local) or bool(activities)
    stages=[
        phase('capture','Capture','done' if form else 'unverified','Linked form submission' if form else 'Form event not linked (event imports may not have one)'),
        phase('qualify','Qualification','done','MQL event recorded in the supplied CRM extract'),
        phase('route','Ownership','done' if assigned else 'attention',f"Assigned to {assigned}" if assigned else 'No current AD owner'),
        phase('notify','Notification','done' if delivered else 'attention','At least one delivered notification in CSV' if delivered else 'No delivered notification in source history'),
        phase('context','Context validation','attention' if lead.get('trust_flags') or not lead.get('account_id') else 'done','Review data-quality flags and account match' if lead.get('trust_flags') or not lead.get('account_id') else 'Account linked; further enrichment not verifiable'),
        phase('act','Seller action','done' if activity else 'pending','Source activity or locally logged seller action' if activity else 'No seller activity matched to this MQL interval'),
        phase('feedback','Marketing feedback','done' if local else 'pending',f'{len(local)} locally recorded action(s) visible to Marketing' if local else 'Awaiting LeadLoop action record')]
    return stages

def ad_metrics(data:dict, merged:pd.DataFrame, actor:str, quarter:str='Q3'):
    """MQL historical metrics with contact-scoped activity constrained to each event interval."""
    scoped=merged[merged.effective_owner.eq(actor)].copy()
    if quarter!='ALL':scoped=scoped[scoped.quarter.eq(quarter)]
    ids=set(scoped.mql_id.astype(str))
    total=len(scoped)
    act=data['ad_activity']
    own_act=act[act.user_id.astype(str).eq(actor)].copy()
    own_act['timestamp']=pd.to_datetime(own_act.timestamp,utc=True)
    intervals=scoped[['mql_id','contact_id','mql_timestamp','next_mql_at','sla_deadline']].copy()
    hits=own_act.merge(intervals,on='contact_id',how='inner')
    if len(hits):
        hits=hits[(hits.timestamp>=hits.mql_timestamp)&(hits.next_mql_at.isna()|(hits.timestamp<hits.next_mql_at))]
        first=hits.groupby('mql_id').timestamp.min()
    else:first=pd.Series(dtype='datetime64[ns, UTC]')
    first_hit=scoped.mql_id.map(first)
    activity_observed=first_hit.notna()
    on_time=first_hit.notna() & (first_hit<=scoped.sla_deadline)
    latency_hours=(first_hit-scoped.mql_timestamp).dt.total_seconds()/3600
    action_count=int(scoped.demo_action_count.sum())
    def percent(a,b):return round(100*a/b,1) if b else 0.0
    # Anchor aging to extraction date: these are historical files, not a live feed.
    anchor=pd.Timestamp('2026-10-08T00:00:00Z')
    unresolved=scoped[scoped.effective_status.astype(str).eq('MQL')].copy()
    overdue=int((unresolved.sla_deadline<anchor).sum())
    by_form=scoped.groupby('form_kind',dropna=False).agg(mqls=('mql_id','size'),reported_worked=('reported_worked','sum')).reset_index().sort_values('mqls',ascending=False)
    by_form['activity_within_sla']=by_form.form_kind.map(scoped.assign(actor_on_time=on_time).groupby('form_kind').actor_on_time.sum()).fillna(0).astype(int)
    by_form=by_form.head(9)
    by_month=scoped.copy()
    by_month['month']=by_month.mql_timestamp.dt.strftime('%Y-%m')
    by_month['actor_on_time']=on_time
    trend=by_month.groupby('month').agg(received=('mql_id','size'),status_worked=('reported_worked','sum'),own_activity_on_time=('actor_on_time','sum')).reset_index().sort_values('month')
    return {'actor':actor,'quarter':quarter,'as_of':'2026-10-08','total':total,
            'historical_status_worked':int(scoped.reported_worked.sum()),
            'historical_status_worked_pct':percent(int(scoped.reported_worked.sum()),total),
            'with_own_activity':int(activity_observed.sum()),'within_sla_own_activity':int(on_time.sum()),
            'within_sla_own_activity_pct':percent(int(on_time.sum()),total),
            'median_first_response_hours':round(float(latency_hours[activity_observed].median()),1) if activity_observed.any() else None,
            'unresolved_mql':len(unresolved),'overdue_as_of_extract':overdue,
            'local_action_records':action_count,
            'trend':trend.to_dict('records'),'mix':by_form.to_dict('records'),
            'caveat':'Historical extract through Sep 2026, pulled Oct 8. Activities are user- and contact-scoped to MQL event intervals. Status-worked is defined by any status transition within SLA. Local LeadLoop actions are not counted as historical response.'}
