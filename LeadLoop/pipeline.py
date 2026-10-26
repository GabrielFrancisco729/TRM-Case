"""LeadLoop pipeline: every form submission runs the same nine stages end to end.

Input is the raw Q3 form stream (what actually arrived on the website), not the
MQL table, so the replay shows what the operating design would have done with
the leads the old system lost. Each stage records who executed it
(integration, automation, ai, human), what it decided and why.

Source CSVs stay read-only. AI steps use Claude when configured; otherwise an
offline stand-in with the same output contract runs and is labelled as such.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import re
import pandas as pd
from pandas.tseries.offsets import BDay

import advanced
import ai_assist

PULL=pd.Timestamp('2026-10-08',tz='UTC')
HAND={'demo','contact','partner'}
DEDUPE_DAYS=30
AI_LIVE_CAP=40

EXEC={'integration':'Integration','automation':'Workflow rule','ai':'AI (Claude)','human':'Human'}

# What each box means. The UI shows title + executors; the overlay shows the rest.
STAGES=[
 {'id':'capture','n':'01','title':'Capture','executors':['integration','automation'],
  'summary':'Website form → webhook → LeadLoop event, checked against the form registry.',
  'trigger':'CMS form submit fires a webhook (one event per submission, idempotent on submission_id).',
  'inputs':['form_submissions: submission_id, form_id, contact, source, campaign, product_interest','Form registry: every form ID allowed to create an MQL'],
  'logic':['Persist the raw event before any rule runs (nothing is dropped silently).','Look the form ID up in the registry.','Unknown ID → map by stem (frm_demo_v2 → frm_demo) so the lead keeps moving, and alert Marketing Ops to confirm the mapping.','Unknown ID with no stem match → hold the event and alert; never discard.'],
  'outputs':['Captured event with canonical form type and intent (hand-raiser or content)'],
  'failure':'Unknown form alert fires on the first submission, not at quarter end. In Q3 this would have fired on Aug 5, the day after the CMS migration.',
  'production':'CMS webhook → queue (e.g. SQS/PubSub) → LeadLoop worker. Registry lives in a table Marketing Ops edits.',
  'demo':'Replays form_submissions.csv in timestamp order; the registry is every form ID that ever triggered an MQL.'},
 {'id':'identity','n':'02','title':'Identity & dedupe','executors':['automation'],
  'summary':'Resolve the person in the CRM and fold repeat fills into the open lead.',
  'trigger':'Captured event.',
  'inputs':['contacts: email, account_id, title, seniority, country','Leads opened by this pipeline in the last 30 days'],
  'logic':['Match the contact by ID / email.',f'Same person already has an open lead from the last {DEDUPE_DAYS} days → attach this fill to it as activity instead of creating a new MQL.','Exception: a hand-raiser on top of an open content lead upgrades that lead (new SLA, new alert).'],
  'outputs':['New lead, or activity merged into an existing one'],
  'failure':'Repeat fills no longer inflate MQL counts or ping the AD twice for the same person.',
  'production':'Salesforce contact/lead match by email + domain; merge rules owned by RevOps.',
  'demo':'Contacts come from contacts.csv; the 30-day window runs over the replayed stream.'},
 {'id':'enrich','n':'03','title':'Enrichment','executors':['automation','ai'],
  'summary':'Attach the account; when the CRM has none, AI researches the lead.',
  'trigger':'New lead.',
  'inputs':['accounts: domain, region, fit tier, segment, customer status, AD owner','Email domain and job title'],
  'logic':['CRM account on the contact → use it (fit, segment, region, owner).','No account → match the email domain to accounts.domain.','Still nothing → AI step: classify the email (corporate vs personal), infer persona and seniority from the title, and list exactly what a data provider must fill (company size, industry).','AI never invents firmographics; unknown stays unknown and is shown as such.'],
  'ai':'Claude receives only form type, title, seniority, email domain type and region (no name, no email). Output is validated JSON: persona, seniority, corporate_email, missing fields, rationale.',
  'outputs':['Lead with account context, or an AI enrichment note plus the list of missing fields'],
  'failure':'Model unavailable or invalid output → the lead keeps moving with "enrichment pending"; nothing blocks on AI.',
  'production':'CRM lookup + enrichment provider (Clearbit/Apollo/ZoomInfo) + Claude for title/persona normalization.',
  'demo':'CRM and domain match are real joins on the CSVs. Provider fields are marked "needs provider". AI runs on Claude when a key is set, otherwise an offline stand-in with the same contract.'},
 {'id':'qualify','n':'04','title':'Qualification','executors':['automation','ai','human'],
  'summary':'Rules route what is certain; AI drafts a view on the rest; a person decides.',
  'trigger':'Enriched lead.',
  'inputs':['Intent (form type), customer status, account match, AI enrichment'],
  'logic':['Hand-raiser (demo, contact, partner) → straight to an AD. No rule or model may hold it.','Existing customer → CS handoff with the AD copied.','Matched account, content form → AD queue (today\'s MQL rule, unchanged).','No account → held for Marketing Ops with an AI suggestion (send to AD / enrich / nurture) and its confidence. A human approves.'],
  'ai':'Claude suggests one of Send to AD / Hold for enrichment / Return to nurture with low/medium/high confidence and the evidence it used. Hand-raiser overrides are blocked in code.',
  'human':'Marketing Ops reviews held leads in the Qualification queue; every decision stores the AI suggestion next to it so agreement is measurable.',
  'outputs':['Lane: AD queue, CS handoff, or Marketing review'],
  'failure':'Backtest shows no CRM field predicts a seller rejection, so nothing is auto-rejected.',
  'production':'Rules in config, Claude for the judgement call, review queue in LeadLoop.',
  'demo':'All lanes are computed; held leads appear in the manager Qualification review.'},
 {'id':'validate','n':'05','title':'Validation','executors':['automation'],
  'summary':'Check the owner and the data before anyone is alerted.',
  'trigger':'Lead with a lane.',
  'inputs':['team roster (active, end_date)','territory_changes','opportunities (close date, contact/account link)'],
  'logic':['Apply territory changes effective on or before the lead date.','Owner inactive or not an AD → mark for re-routing.','Open opportunity with a past close date, or a contact that belongs to another account → flag; never used as a buying signal.'],
  'outputs':['Validated owner (or "needs routing") and data-trust flags'],
  'failure':'Leads stop landing on people who left. In Q3, the Sep 1 territory change would have re-routed these leads the same day.',
  'production':'Nightly roster sync + event on territory change; Data guard checks run continuously.',
  'demo':'Uses team.csv end dates and territory_changes.csv effective dates.'},
 {'id':'route','n':'06','title':'Routing','executors':['automation','human'],
  'summary':'Every lead gets a named, active owner. No fallback queue.',
  'trigger':'Validated lead.',
  'inputs':['Validated account owner','Region from account or contact country','Current load per active AD'],
  'logic':['Valid account owner → assign.','Otherwise → least-loaded active AD in the region.','No region at all → manager triage with a 15-minute SLA (a person, not a silent system queue).'],
  'human':'Manager triage only when the region is unknown.',
  'outputs':['Named owner + SLA clock (24h hand-raisers, 5 business days content)'],
  'failure':'"SYS-QUEUE" disappears as a destination; unowned leads become a visible triage task.',
  'production':'Routing service writes owner to Salesforce; manager triage in LeadLoop.',
  'demo':'Load balancing runs over the replay, so the AD lists reflect the whole quarter.'},
 {'id':'notify','n':'07','title':'Notify','executors':['integration','automation'],
  'summary':'Slack alert with action buttons; delivery is confirmed, not assumed.',
  'trigger':'Owner assigned.',
  'inputs':['Owner, regional channel, channel health'],
  'logic':['Post to the regional channel and DM the owner (hand-raisers always DM).','Message carries Accept / Reject / Return buttons, so the AD can act without opening another tool.','Delivery error → retry with backoff → fall back to DM + email → alert Marketing Ops to fix the channel.'],
  'outputs':['Delivered alert (or escalated fallback) with an idempotency key'],
  'failure':'In Q3, #mql-emea was archived on Aug 18 and 61 alerts failed silently. Here the first failure triggers fallback and an alert.',
  'production':'Slack Block Kit interactive messages + outbox worker (already in this repo) + email fallback.',
  'demo':'Channel health comes from notifications_log.csv (channel_not_found). Slack is simulated without a token.'},
 {'id':'workspace','n':'08','title':'AD workspace','executors':['human','ai'],
  'summary':'Ranked inbox: why this lead, what to do, one click to act.',
  'trigger':'Alert delivered.',
  'inputs':['Explainable score, trust flags, lead history, SLA'],
  'logic':['Rank by intent, fit, seniority, validated opportunity and recency (points, not probabilities).','Show why the lead is here and every trust flag.','AD accepts, rejects with a listed reason, or returns it to marketing.'],
  'ai':'On request, Claude drafts a first-touch email or next step. The AD edits and sends; nothing is sent automatically.',
  'human':'The AD makes every decision.',
  'outputs':['Accepted, rejected (reason), or returned lead'],
  'failure':'SLA breach on a hand-raiser escalates to the manager.',
  'production':'LeadLoop inbox + Slack buttons; decisions written back to Salesforce.',
  'demo':'Open the Workspace as any AD to work the list this run built.'},
 {'id':'feedback','n':'09','title':'Feedback loop','executors':['human','automation'],
  'summary':'Decisions flow back to marketing as structured data.',
  'trigger':'AD decision.',
  'inputs':['Decision, reason category, note'],
  'logic':['Rejections need a reason from the list Marketing Ops controls.','Returns go to the Marketing review inbox.','Reasons roll up by campaign and form, so marketing sees which sources ADs reject and why.'],
  'outputs':['Marketing outcomes by source; inputs for rule changes'],
  'failure':'One shared definition of "worked": an AD decision, not any status change. Robot status changes do not count.',
  'production':'Salesforce status + reason sync; weekly review between Marketing Ops and Sales.',
  'demo':'Decisions from the Workspace and the Slack preview land here immediately.'},
]


def _ts(v):
    return pd.to_datetime(v,utc=True) if v is not None and not (isinstance(v,float) and pd.isna(v)) and str(v)!='' else None


def _s(v):
    return None if v is None or (not isinstance(v,(list,dict)) and pd.isna(v)) else v


def _kind(form_id):
    return re.sub(r'_v\d+$','',str(form_id).replace('frm_','')) if form_id else 'badge_scan'


def _step(stage,executor,status,title,detail=''):
    return {'stage':stage,'executor':executor,'status':status,'title':title,'detail':detail}


def _active(team,user,at):
    if user not in team.index:return False
    t=team.loc[user]
    if t.role!='AD':return False
    end=_ts(_s(t.end_date))
    if end is not None and at>=end:return False
    return str(t.active).lower()=='true' or end is not None


def offline_review(lead):
    """Same contract as ai_assist.pipeline_review; deterministic, labelled offline."""
    title=str(lead.get('title') or '')
    low=title.lower()
    seniority=lead.get('seniority') or ('C-level' if re.search(r'\b(chief|cfo|cio|ciso|ceo|cto|coo)\b',low) else 'VP' if 'vp' in low or 'vice president' in low else 'Director' if 'director' in low or 'head' in low else 'Manager' if 'manager' in low or 'lead' in low else 'IC' if title else None)
    persona='Security' if re.search(r'secur|ciso|risk|fraud',low) else 'Compliance' if re.search(r'complian|aml|kyc|sanction|regulat',low) else 'Finance' if re.search(r'financ|cfo|treasur',low) else 'Other' if title else 'Unknown'
    corporate=not lead.get('personal_email')
    missing=['company size','industry'] + ([] if title else ['job title'])
    if corporate and seniority in ('C-level','VP','Director'):
        sug,conf='Hold for enrichment','medium';why=f'Corporate email and a {seniority} {persona.lower()} title, but no CRM account: enrich the company before routing.'
    elif not corporate and not title:
        sug,conf='Return to nurture','low';why='Personal email and no title: too little to judge. Keep in nurture unless a provider finds the company.'
    else:
        sug,conf='Hold for enrichment','low';why='No CRM account and limited signals; a provider lookup should decide.'
    return {'persona':persona,'seniority':seniority,'corporate_email':corporate,'missing':missing,'suggestion':sug,'confidence':conf,'rationale':why,'source':'offline_stand_in'}


def acquisition_value(d,m):
    """Pipeline at risk, from observed MQL→opportunity creation within 120 days."""
    o=d['opportunities'].copy();o['created']=pd.to_datetime(o.created_date,utc=True)
    nb=o[o.type.eq('New Business')]
    x=m[['mql_id','contact_id','mql_timestamp','hand_raiser']].merge(o[['contact_id','created']],on='contact_id',how='left')
    x['hit']=(x.created>=x.mql_timestamp)&(x.created<=x.mql_timestamp+pd.Timedelta(days=120))
    per=x.groupby('mql_id').agg(hit=('hit','max'),hand=('hand_raiser','first'))
    rate_h=float(per[per.hand].hit.mean());rate_c=float(per[~per.hand].hit.mean())
    n_h=int(per[per.hand].hit.sum());n_c=int(per[~per.hand].hit.sum())
    avg=float(nb.amount.mean())
    q3=m[m.quarter.eq('Q3')]
    delivery=q3[q3.unassigned_at_creation | ~q3.alert_delivered | (q3.owner_active_today==False)]
    f=d['form_submissions'];registry=set(d['mql_events'].trigger_form_id.dropna())
    lost_h=int(((pd.to_datetime(f.timestamp,utc=True).dt.month>6)&~f.form_id.isin(registry)&f.form_id.map(_kind).isin(HAND)).sum())
    fail_h=int(delivery.hand_raiser.sum());fail_c=len(delivery)-fail_h
    opps=lost_h*rate_h+fail_h*rate_h+fail_c*rate_c
    return {'rate_hand_raiser':round(100*rate_h,1),'rate_content':round(100*rate_c,1),
            'opps_seen_hand':n_h,'opps_seen_content':n_c,'avg_new_business':round(avg),
            'lost_hand_raisers':lost_h,'delivery_risk_leads':len(delivery),'delivery_risk_hand':fail_h,
            'expected_opps':round(opps,1),'pipeline_at_risk':round(opps*avg,-3),
            'basis':'Rate = share of MQLs whose contact got an opportunity within 120 days (both quarters). Value = average New Business amount. Small samples (8 hand-raiser and 46 content opportunities): read as order of magnitude, not a forecast.'}


def run(d,m,weights,ai_mode='auto',quarter='Q3',ai_cache=None):
    ai_cache=ai_cache if ai_cache is not None else {}
    live=ai_mode!='offline' and ai_assist.configured()
    team=d['team'].set_index('user_id')
    contacts=d['contacts'].set_index('contact_id')
    accounts=d['accounts'].set_index('account_id')
    domains={str(r.domain).lower():i for i,r in accounts.iterrows() if _s(r.domain)}
    country_region=d['accounts'].dropna(subset=['country']).drop_duplicates('country').set_index('country').region.to_dict()
    terr=d['territory_changes'].copy();terr['eff']=pd.to_datetime(terr.effective_date,utc=True)
    registry=set(d['mql_events'].trigger_form_id.dropna())
    notif=d['notifications_log']
    bad=notif[notif.delivered.astype(str).str.lower().ne('true')]
    archived={ch:pd.to_datetime(g.sent_at,utc=True).min() for ch,g in bad.groupby('channel')}
    mql_by_sub=m.set_index('trigger_submission_id') if 'trigger_submission_id' in m else None
    ads=team[(team.role=='AD')]
    load={u:0 for u in ads.index}

    forms=d['form_submissions'].copy()
    forms['ts']=pd.to_datetime(forms.timestamp,utc=True)
    q=forms.ts.dt.month<=6
    forms=forms[~q] if quarter=='Q3' else forms[q]
    forms=forms.sort_values('ts')

    open_leads={}  # contact_id -> (ts, sid, hand)
    traces={};leads=[];held_for_ai=[]
    for s in forms.itertuples(index=False):
        steps=[];ts=s.ts;sid=s.submission_id
        hist=mql_by_sub.loc[sid] if mql_by_sub is not None and sid in mql_by_sub.index else None
        if isinstance(hist,pd.DataFrame):hist=hist.iloc[0]
        kind=_kind(s.form_id);hand=kind in HAND
        # 01 capture
        if s.form_id in registry:
            steps.append(_step('capture','integration','ok','Webhook received · form registered',f'{s.form_id} is on the trigger list ({s.form_name}).'))
            unknown=False
        else:
            steps.append(_step('capture','automation','fixed','Unknown form ID → mapped by stem + ops alerted',f'{s.form_id} is not in the registry. Stem rule maps it to "{kind}" ({"hand-raiser" if hand else "content"}). Old system: no MQL was created.'))
            unknown=True
        # 02 identity
        c=contacts.loc[s.contact_id] if s.contact_id in contacts.index else None
        name=f"{_s(c.first_name) or ''} {_s(c.last_name) or ''}".strip() if c is not None else 'Unknown contact'
        prev=open_leads.get(s.contact_id)
        if prev and (ts-prev[0]).days<DEDUPE_DAYS and not (hand and not prev[2]):
            steps.append(_step('identity','automation','merged','Repeat fill → merged into open lead',f'Same contact opened {prev[1]} {(ts-prev[0]).days} days ago. Logged as activity; no new MQL, no second alert.'))
            traces[sid]=steps
            leads.append({'sid':sid,'name':name,'account':None,'form':kind,'hand':hand,'ts':ts.isoformat(),'outcome':'Merged','owner':None,'score':None,'unknown_form':unknown,'hist':_hist(hist)})
            continue
        if prev and hand and not prev[2]:
            steps.append(_step('identity','automation','ok','Contact matched · hand-raiser upgrades open lead',f'Open content lead {prev[1]} is upgraded: new 24h SLA and a fresh alert.'))
        else:
            steps.append(_step('identity','automation','ok','Contact matched in CRM',f'{name} · {_s(c.title) if c is not None else "—"}'))
        open_leads[s.contact_id]=(ts,sid,hand)
        # 03 enrichment
        acc_id=_s(c.account_id) if c is not None else None
        email=str(_s(c.email) or '').lower() if c is not None else ''
        dom=email.rsplit('@',1)[-1] if '@' in email else ''
        personal=dom in advanced.FREEMAIL
        ai=None
        if acc_id:
            a=accounts.loc[acc_id]
            steps.append(_step('enrich','automation','ok','CRM account attached',f'{a.account_name} · fit {_s(a.fit_tier) or "unscored"} · {a.segment} · {a.region}'))
        elif dom in domains:
            acc_id=domains[dom];a=accounts.loc[acc_id]
            steps.append(_step('enrich','automation','fixed','Account found by email domain',f'{dom} → {a.account_name}'))
        else:
            a=None
            payload={'form_kind':kind,'title':_s(c.title) if c is not None else None,'seniority':_s(c.seniority) if c is not None else None,
                     'personal_email':personal,'region':country_region.get(_s(c.country)) if c is not None else None}
            if sid in ai_cache: ai=ai_cache[sid]
            else: ai=offline_review(payload)
            if live and sid not in ai_cache: held_for_ai.append((sid,payload))
            steps.append(_step('enrich','ai','ai','No CRM account → AI enrichment',''))
        # 04 qualification
        cust=a is not None and str(a.customer_status)=='Customer'
        if hand:
            lane='AD';steps.append(_step('qualify','automation','ok','Hand-raiser → AD (protected)','Demo/contact/partner requests always reach an AD within 24h. No rule or model can hold them.'))
        elif cust:
            lane='CS';steps.append(_step('qualify','automation','route','Existing customer → CS handoff',f'{a.account_name} is a customer; CS owns the reply, the AD is copied.'))
        elif a is not None:
            lane='AD';steps.append(_step('qualify','automation','ok','Matched account · content intent → AD','Same rule as today\'s MQL definition, now with a named owner guaranteed.'))
        else:
            lane='REVIEW';steps.append(_step('qualify','ai','hold','AI suggestion → Marketing Ops decides',''))
        # 05 validation + 06 routing + 07 notify
        owner=None
        region=(_s(a.region) if a is not None else None) or (country_region.get(_s(c.country)) if c is not None else None)
        flags=[]
        if lane in ('AD','CS'):
            cand=_s(a.ad_owner) if a is not None else None
            tc=terr[terr.account_id==acc_id] if a is not None else terr.iloc[0:0]
            tc=tc.iloc[-1] if len(tc) else None
            if tc is not None and ts<tc.eff:
                # Before the change the old AD owned the account; he leaves on the effective date.
                old=tc.old_ad
                steps.append(_step('validate','automation','fixed','Owner leaves on territory change → revalidated',
                    f'{old} owned this account on {ts.date()} but leaves on {tc.effective_date}. The revalidation job moves open leads to {tc.new_ad} that day, and the new owner is alerted.'))
                owner=tc.new_ad if _active(team,tc.new_ad,tc.eff) else None;how='Territory change (revalidated)'
            elif tc is not None and hist is not None and _s(hist.owner_at_mql)==tc.old_ad:
                steps.append(_step('validate','automation','fixed','Stale routing table caught → territory change applied',
                    f'The old routing still pointed to {tc.old_ad}, inactive since {tc.effective_date}. Validation applies the FY27 territory: {tc.new_ad}.'))
                owner=tc.new_ad;how='Territory change applied'
            elif cand and _active(team,cand,ts):
                steps.append(_step('validate','automation','ok','Owner verified',f'{cand} is an active AD on {ts.date()}.'))
                owner=cand;how='Account owner'
            else:
                why='the account has no AD owner or territory' if not cand else f'{cand} is not an active AD on {ts.date()}'
                steps.append(_step('validate','automation','fixed','Owner invalid → re-route',why[0].upper()+why[1:]+'.'))
            if a is not None:
                o=d['opportunities'];oo=o[o.account_id.eq(acc_id)]
                if len(oo) and (pd.to_datetime(oo.close_date)<PULL.tz_localize(None)).any():flags.append('Stale opportunity: not scored')
            if not owner and region:
                pool=[u for u in ads.index if team.loc[u].region==region and _active(team,u,ts)]
                if pool:
                    owner=min(pool,key=lambda u:(load[u],u));how=f'Least-loaded active {region} AD'
            if owner:
                load[owner]+=1
                steps.append(_step('route','automation','ok' if how in ('Account owner','Territory change (revalidated)','Territory change applied') else 'fixed',f'Routed to {team.loc[owner]["name"]} ({owner})',how+'.'))
                ch=f'#mql-{str(region or team.loc[owner].region).lower()}'
                if ch in archived and ts>=archived[ch]:
                    steps.append(_step('notify','integration','fixed','Channel archived → DM + email fallback, ops alerted',f'Slack returned channel_not_found for {ch} (archived {archived[ch].date()}). Retried 3×, then DM to {owner} and email; Marketing Ops alerted to restore the channel.'))
                else:
                    steps.append(_step('notify','integration','ok',f'Alert delivered · {ch}' + (' + DM' if hand else ''),'Message carries Accept / Reject / Return buttons.'))
                sla=ts+pd.Timedelta(hours=24) if hand else ts+BDay(5)
                steps.append(_step('workspace','human','pending',f'In {owner}\'s inbox · SLA {sla.strftime("%b %d %H:%M")}','Ranked by explainable points; AI can draft the first touch on request.'))
                outcome='CS handoff' if lane=='CS' else 'AD inbox'
            else:
                steps.append(_step('route','human','hold','No territory → manager triage (15-min SLA)','Neither the account nor the contact has a region. A manager assigns it within 15 minutes; it never sits in a silent system queue.'))
                outcome='Manager triage'
        else:
            steps.append(_step('validate','automation','skip','Waits for the qualification decision',''))
            outcome='Marketing review'
        traces[sid]=steps
        leads.append({'sid':sid,'name':name,'account':_s(a.account_name) if a is not None else None,'acc_id':acc_id,'form':kind,'hand':hand,
                      'ts':ts.isoformat(),'outcome':outcome,'owner':owner,'region':region,'customer':cust,'flags':flags,'unknown_form':unknown,
                      'title':_s(c.title) if c is not None else None,'seniority':_s(c.seniority) if c is not None else None,'email':email or None,
                      'personal':personal,'fit':_s(a.fit_tier) if a is not None else None,'product':_s(s.product_interest),'ai':ai,'hist':_hist(hist)})

    if held_for_ai:
        def call(item):
            sid,payload=item
            try:return sid,ai_assist.pipeline_review(payload)
            except RuntimeError:return sid,None
        with ThreadPoolExecutor(6) as pool:
            for sid,res in pool.map(call,held_for_ai[:AI_LIVE_CAP]):
                if res:ai_cache[sid]=res
        for l in leads:
            if l['sid'] in ai_cache:l['ai']=ai_cache[l['sid']]
    for l in leads:
        if l.get('ai'):_fill_ai(traces[l['sid']],l['ai'])

    _score(leads,d,weights)
    return _summary(d,m,leads,traces,live,quarter),traces


def _hist(h):
    if h is None:return {'mql':False}
    return {'mql':True,'mql_id':_s(h.get('mql_id')),
            'owner_at_mql':_s(h.owner_at_mql),'unassigned':bool(h.unassigned_at_creation),'delivered':bool(h.alert_delivered),
            'owner_active_today':_s(h.owner_active_today),'customer':str(h.customer_status)=='Customer','worked':bool(h.reported_worked)}


def _fill_ai(steps,ai):
    src='Claude' if ai.get('source')=='claude' else 'Offline stand-in (no API key)'
    for st in steps:
        if st['stage']=='enrich' and st['executor']=='ai':
            st['title']=f'No CRM account → AI enrichment · {src}'
            st['detail']=f"Persona {ai.get('persona')}, seniority {ai.get('seniority') or 'unknown'}, {'corporate' if ai.get('corporate_email') else 'personal'} email. Missing for a provider: {', '.join(ai.get('missing') or [])}."
        if st['stage']=='qualify' and st['executor']=='ai':
            st['title']=f"AI suggests “{ai.get('suggestion')}” ({ai.get('confidence')} confidence) → Marketing Ops decides"
            st['detail']=f"{ai.get('rationale')} Source: {src}. Confidence is qualitative, not a probability."


def _score(leads,d,weights):
    rows=[l for l in leads if l['outcome'] in ('AD inbox','CS handoff','Manager triage')]
    if not rows:return
    df=pd.DataFrame([{'form_kind':l['form'],'fit_tier':l.get('fit'),'priority':'P1' if l['hand'] else 'P2','seniority':l.get('seniority'),
                      'account_id':l.get('acc_id'),'product_interest':l.get('product'),'email':l.get('email'),'title':l.get('title'),
                      'customer_status':'Customer' if l.get('customer') else 'Prospect','source':'form','mql_timestamp':pd.Timestamp(l['ts'])} for l in rows])
    scored=advanced.evidence_score(df,d,weights)
    for l,(sc,ex,tf) in zip(rows,zip(scored.queue_score,scored.score_explanation,scored.trust_flags)):
        l['score']=int(sc);l['why']=ex;l['flags']=sorted(set(l.get('flags',[])+list(tf)))


def _summary(d,m,leads,traces,live,quarter):
    df=pd.DataFrame(leads)
    team=d['team'].set_index('user_id')
    stages=[]
    for st in STAGES:
        seen=[s for t in traces.values() for s in t if s['stage']==st['id']]
        by={};ex={}
        for s in seen:by[s['status']]=by.get(s['status'],0)+1;ex[s['executor']]=ex.get(s['executor'],0)+1
        stages.append({'id':st['id'],'in':len(seen),'by_status':by,'by_executor':ex})
    outcomes=df.outcome.value_counts().to_dict()
    inbox=df[df.outcome.isin(['AD inbox','CS handoff'])]
    ad_lists=[]
    for owner,g in inbox[inbox.outcome.eq('AD inbox')].groupby('owner'):
        g=g.sort_values('score',ascending=False)
        ad_lists.append({'ad':owner,'name':team.loc[owner]['name'],'region':team.loc[owner].region,'count':len(g),'hand':int(g.hand.sum()),
                         'top':[{'sid':r.sid,'name':r.name,'account':r.account,'form':r.form,'score':r.score} for r in g.head(3).itertuples()]})
    ad_lists.sort(key=lambda x:(-x['hand'],-x['count']))
    q3=m[m.quarter.eq(quarter)]
    reached_actual=int((q3.owner_at_mql.notna()&q3.owner_at_mql.ne('SYS-QUEUE')&q3.alert_delivered&(q3.owner_active_today!=False)&q3.customer_status.ne('Customer')).sum())
    hand_total=int(df.hand.sum());hand_actual=int(q3.form_kind.isin(HAND).sum())
    fixed=lambda stage:sum(1 for t in traces.values() for s in t if s['stage']==stage and s['status']=='fixed')
    compare=[
     {'metric':'Hand-raisers that became an actionable lead','actual':f'{hand_actual} of {hand_total}','leadloop':f'{int(df[df.hand & df.outcome.ne("Merged")].shape[0])} of {hand_total}','note':'Unknown CMS form IDs are mapped and alerted instead of dropped.'},
     {'metric':'Leads that reached an active AD with a delivered alert','actual':str(reached_actual),'leadloop':str(int(df.outcome.eq('AD inbox').sum())),'note':'Owner verified, fallback delivery, no system queue.'},
     {'metric':'Leads created with no named owner','actual':str(int(q3.unassigned_at_creation.sum())),'leadloop':str(int(df.outcome.eq('Manager triage').sum())),'note':'Only region-less contacts go to a manager, with a 15-minute SLA.'},
     {'metric':'Alerts that failed silently','actual':str(int((~q3.alert_delivered & q3.alert_failed).sum())),'leadloop':'0','note':f'{fixed("notify")} alerts rerouted to DM + email after #mql-emea was archived.'},
     {'metric':'Leads owned by someone who had left','actual':str(int((q3.owner_active_today==False).sum())),'leadloop':'0','note':f'{fixed("validate")} owners corrected at validation (territory change or departed AD).'},
     {'metric':'Existing customers sent to an AD as new MQLs','actual':str(int(q3.customer_status.eq('Customer').sum())),'leadloop':'0','note':f'{int(df.outcome.eq("CS handoff").sum())} routed to CS with the AD copied.'},
     {'metric':'Repeat fills counted as new MQLs','actual':str(int(q3.duplicated('contact_id').sum())),'leadloop':'0','note':f'{int(df.outcome.eq("Merged").sum())} folded into the open lead.'},
     {'metric':'Unmatched content leads','actual':'Dropped or sent to AD unqualified','leadloop':str(int(df.outcome.eq('Marketing review').sum())),'note':'Held for Marketing Ops with an AI suggestion; a human decides.'},
    ]
    sh=d['mql_status_history'];robot=sh[sh.changed_by.eq('lead_routing_tool')]
    first_v2=df[df.unknown_form].ts.min()
    notif=d['notifications_log'];bad=notif[notif.delivered.astype(str).str.lower().ne('true')]
    monitors=[
     {'date':str(pd.to_datetime(robot.changed_at,utc=True).min().date()) if len(robot) else None,'change':'Jul 6 · auto-convert rule added to the routing tool','alert':'Status changed by a robot, not a seller','action':'Excluded from "worked"; Sales and Marketing see the same number.','count':len(robot)},
     {'date':str(pd.Timestamp(first_v2).date()) if first_v2 else None,'change':'Aug 4 · CMS migrated, forms rebuilt','alert':'Unknown form ID frm_demo_v2 on the first submission','action':'Lead mapped by stem and routed; Marketing Ops confirms the registry entry.','count':int(df.unknown_form.sum())},
     {'date':str(pd.to_datetime(bad.sent_at,utc=True).min().date()) if len(bad) else None,'change':'Aug 18 · Slack cleanup archived inactive channels','alert':'channel_not_found on #mql-emea','action':'Retry → DM + email fallback; ops restores the channel.','count':len(bad)},
     {'date':'2026-09-01','change':'Sep 1 · FY27 territory changes','alert':'Owner revalidation on territory change','action':'Leads on moved accounts re-routed to the new active owner the same day.','count':int(len(d['territory_changes']))},
    ]
    examples=[]
    def pick(label,mask):
        hit=df[mask]
        if len(hit):examples.append({'label':label,'sid':hit.iloc[0].sid,'name':hit.iloc[0]['name']})
    pick('Hand-raiser on a new CMS form (lost in Q3)',df.unknown_form & df.hand)
    has=lambda stage,status:df.sid.map(lambda s:any(x['stage']==stage and x['status']==status for x in traces[s]))
    pick('EMEA lead after the Slack channel was archived',has('notify','fixed') & df.hand)
    pick('Account that changed territory on Sep 1',has('validate','fixed'))
    pick('No CRM account: AI enrichment + human review',df.outcome.eq('Marketing review'))
    pick('Existing customer routed to CS',df.outcome.eq('CS handoff'))
    pick('Repeat fill merged into the open lead',df.outcome.eq('Merged'))
    compact=df[['sid','name','account','form','hand','ts','outcome','owner','score','unknown_form']].where(df[['sid','name','account','form','hand','ts','outcome','owner','score','unknown_form']].notna(),None)
    return {'ran_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),'quarter':quarter,'input':len(df),
            'ai_mode':'claude' if live else 'offline','ai_used':int(sum(1 for l in leads if l.get('ai'))),
            'ai_claude':int(sum(1 for l in leads if (l.get('ai') or {}).get('source')=='claude')),
            'stages':stages,'outcomes':outcomes,'ad_lists':ad_lists,'compare':compare,'monitors':monitors,'examples':examples,
            'leads':[{k:(None if (isinstance(v,float) and pd.isna(v)) else v) for k,v in r.items()} for r in compact.to_dict(orient='records')]}
