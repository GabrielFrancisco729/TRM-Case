"""Quillon LeadLoop: grounded diagnostics + human-led queue + action audit + connectors.
Run: python server.py (localhost:7860). This is a demo, not production identity or CRM sync.
"""
from __future__ import annotations
import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
import threading
from typing import Literal

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core import load_data, enriched, volume_waterfall, not_worked_breakdown, get_actions, init_db, add_action, overlay_actions
from integration import SlackAdapter, DeliveryError, slack_payload, slack_configured
from ai_assist import configured as ai_configured, recommend as ai_recommend, next_best_action as ai_next_best_action
from ad_experience import ad_metrics, journey_for_lead, next_action_rule
import pipeline
from advanced import (setup as setup_advanced, config as scoring_config, set_config, undo_config, DEFAULT_WEIGHTS, BOUNDS,
                      evidence_score, operating_breakdown, risk_checks, qualification_list, record_review,team_comparison, qualification_backtest,
                      VALID_DECISIONS, control_group, acquisition_source_evidence, get_rejection_reasons, set_rejection_reasons, undo_rejection_reasons)

BASE=Path(__file__).resolve().parent
# Load local, private credentials once at startup; OS environment variables take precedence.
load_dotenv(dotenv_path=BASE / '.env', override=False)
DATA=BASE/'data'
# Optional persistent disk mount (paid Render plans); local/default state remains unchanged.
DB=Path(os.getenv('LEADLOOP_STATE_DIR') or (BASE/'state'))/'leadloop.sqlite3'
SECRET=(os.getenv('SESSION_SECRET') or secrets.token_urlsafe(32)).encode()
MANAGER_PIN=os.getenv('MANAGER_DEMO_PIN','quillon-demo')
APP_PORT=int(os.getenv('PORT','7860'))
APP_LOCK=threading.Lock()

def state_connection():
    conn=sqlite3.connect(DB,timeout=8)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA busy_timeout=8000')
    return conn


def ensure_state():
    DB.parent.mkdir(parents=True,exist_ok=True)
    init_db(DB)
    with state_connection() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS seen_mqls (mql_id TEXT PRIMARY KEY, first_seen_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS notification_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_key TEXT UNIQUE NOT NULL,
            mql_id TEXT NOT NULL, channel TEXT NOT NULL, payload TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            due_at TEXT DEFAULT CURRENT_TIMESTAMP, last_error TEXT, external_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, processed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_due ON notification_outbox(state,due_at);
        CREATE TABLE IF NOT EXISTS pipeline_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at TEXT NOT NULL,
            summary_json TEXT NOT NULL, traces_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pipeline_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), run_id INTEGER NOT NULL,
            submission_id TEXT NOT NULL, actor TEXT NOT NULL, decision TEXT NOT NULL, reason_code TEXT,
            note TEXT NOT NULL DEFAULT '', via TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pipeline_ai_cache (submission_id TEXT PRIMARY KEY, result_json TEXT NOT NULL);
        ''')
        setup_advanced(conn)
        # Recover notifications abandoned mid-flight (e.g. process died after claiming a job).
        conn.execute("UPDATE notification_outbox SET state='retrying',last_error='Recovered expired processing lease' WHERE state='processing' AND due_at < CURRENT_TIMESTAMP")
        # Baseline the historical data; never blast Slack with old leads at startup.
        # New MQL IDs subsequently detected in the CSV are enqueued once.
        if conn.execute('SELECT COUNT(*) FROM seen_mqls').fetchone()[0]==0:
            data=load_data(DATA)
            conn.executemany('INSERT OR IGNORE INTO seen_mqls(mql_id) VALUES (?)',[(str(x),) for x in data['mql_events'].mql_id])


def source():
    data=load_data(DATA)
    with state_connection() as conn:
        weights=scoring_config(conn)
    return data, evidence_score(enriched(data),data,weights)


def json_value(x):
    if x is None: return None
    if isinstance(x,(np.bool_,bool)): return bool(x)
    if isinstance(x,(np.integer,)): return int(x)
    if isinstance(x,(np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x,(pd.Timestamp,datetime)): return x.isoformat() if not pd.isna(x) else None
    if isinstance(x,(np.ndarray,list,tuple)): return [json_value(i) for i in x]
    if isinstance(x,dict): return {str(k):json_value(v) for k,v in x.items()}
    if pd.isna(x): return None
    return x


def as_records(df:pd.DataFrame):
    return [json_value(x) for x in df.to_dict(orient='records')]


def signed_session(user_id, role):
    data={'sub':user_id,'role':role,'exp':int(time.time()+8*3600)}
    encoded=base64.urlsafe_b64encode(json.dumps(data,separators=(',',':')).encode()).decode().rstrip('=')
    sig=hmac.new(SECRET,encoded.encode(),hashlib.sha256).hexdigest()
    return encoded+'.'+sig


def read_session(request:Request):
    token=request.cookies.get('ql_session','')
    try:
        encoded,sig=token.rsplit('.',1)
        if not hmac.compare_digest(hmac.new(SECRET,encoded.encode(),hashlib.sha256).hexdigest(),sig): return None
        data=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if data.get('exp',0)<time.time(): return None
        return data
    except (ValueError,AttributeError,KeyError,TypeError): return None


def require_user(request:Request, role=None):
    session=read_session(request)
    if not session: raise HTTPException(401,'Sign in required')
    if role and session['role']!=role: raise HTTPException(403,'Insufficient permissions')
    return session


def known_ads(d):
    t=d['team']
    return as_records(t[(t.role=='AD')&(t.active.astype(str).str.lower()=='true')][['user_id','name','region']].sort_values(['region','name']))


def overlay(d,m):
    df=overlay_actions(m,get_actions(DB))
    # Manager qualification decisions are append-only local workflow state.
    # They do not establish that a Salesforce or CS synchronization occurred.
    with state_connection() as conn:
        setup_advanced(conn)
        rows=conn.execute('''SELECT mql_id,decision,reason FROM qualification_reviews
          WHERE id IN (SELECT MAX(id) FROM qualification_reviews GROUP BY mql_id)''').fetchall()
    by_id={r['mql_id']:dict(r) for r in rows}
    df['qualification_approved']=df.mql_id.map(lambda x:by_id.get(x,{}).get('decision'))
    df['qualification_approval_reason']=df.mql_id.map(lambda x:by_id.get(x,{}).get('reason'))
    df['needs_ad_action']=(df.needs_ad_action & ~df.qualification_approved.isin(
        ['Send to CS','Hold for enrichment','Return to nurture']))
    return df


def queue_rows(df, actor, quarter, state, search, lane='ALL'):
    df=df[df.effective_owner.eq(actor)].copy()
    if quarter != 'ALL': df=df[df.quarter.eq(quarter)]
    if state=='needs_action': df=df[df.needs_ad_action]
    if state=='actioned': df=df[~df.needs_ad_action]
    if lane!='ALL': df=df[df.queue_lane.eq(lane)]
    if search:
        term=search.casefold()
        hay=df[['contact_name','account_name','mql_id','email','form_kind']].fillna('').astype(str).agg(' '.join,axis=1)
        df=df[hay.str.casefold().str.contains(term,regex=False)]
    df=df.sort_values(['queue_score','mql_timestamp'],ascending=[False,False])
    fields=['mql_id','contact_name','account_name','form_kind','queue_score','score_explanation','trust_flags','qualification_suggestion','qualification_approved','queue_lane','effective_status','mql_timestamp','priority','quarter','data_warning','demo_action_count','needs_ad_action','region','effective_owner','email']
    return df[fields]


def insert_notification(conn, mql:dict, reason:str='new'):
    payload=slack_payload(mql)
    event_key=f'{reason}:{mql["mql_id"]}'
    cur=conn.execute('''INSERT OR IGNORE INTO notification_outbox(event_key,mql_id,channel,payload) VALUES(?,?,?,?)''',
                     (event_key,mql['mql_id'],payload['channel'],json.dumps(payload)))
    return bool(cur.rowcount)


def poll_new_mqls():
    d,m=source()
    with APP_LOCK, state_connection() as conn:
        seen={r['mql_id'] for r in conn.execute('SELECT mql_id FROM seen_mqls')}
        new=m[~m.mql_id.isin(seen)]
        for row in as_records(new):
            insert_notification(conn,row)
            conn.execute('INSERT OR IGNORE INTO seen_mqls(mql_id) VALUES(?)',(row['mql_id'],))
        conn.commit()
    return len(new)


def deliver_due():
    sent=0
    sink=SlackAdapter()
    for i in range(20):
        # Lease one job at a time, so parallel worker processes don't pick the same job.
        with state_connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("""SELECT * FROM notification_outbox
              WHERE state IN ('pending','retrying') AND due_at <= CURRENT_TIMESTAMP
              ORDER BY id LIMIT 1""").fetchone()
            if not row:
                conn.commit();break
            conn.execute("UPDATE notification_outbox SET state='processing',attempts=attempts+1,due_at=datetime('now','+2 minutes') WHERE id=?",(row['id'],))
            conn.commit()
        try:
            receipt=sink.post(json.loads(row['payload']))
            with state_connection() as conn:
                conn.execute("UPDATE notification_outbox SET state=?,external_id=?,last_error=NULL,processed_at=CURRENT_TIMESTAMP WHERE id=?",
                             (receipt.status,receipt.external_id,row['id']))
                conn.commit()
            sent+=1
        except DeliveryError as exc:
            tries=int(row['attempts'])+1
            delay=exc.retry_after if exc.retry_after is not None else min(3600,30*(2**(tries-1)))
            status='failed' if exc.permanent or tries>=5 else 'retrying'
            with state_connection() as conn:
                conn.execute("UPDATE notification_outbox SET state=?,last_error=?,due_at=datetime('now',?||' seconds'),processed_at=CURRENT_TIMESTAMP WHERE id=?",
                             (status,str(exc),f'+{delay}',row['id']))
                conn.commit()
        except Exception as exc:
            # Keep the worker alive and the error visible. No token or payload in diagnostics.
            tries=int(row['attempts'])+1
            delay=min(3600,30*(2**(tries-1)))
            with state_connection() as conn:
                conn.execute("UPDATE notification_outbox SET state=?,last_error=?,due_at=datetime('now',?||' seconds'),processed_at=CURRENT_TIMESTAMP WHERE id=?",
                            ('failed' if tries>=5 else 'retrying',type(exc).__name__,f'+{delay}',row['id']))
                conn.commit()
    return sent


async def background_jobs():
    while True:
        try:
            await asyncio.to_thread(poll_new_mqls)
            await asyncio.to_thread(deliver_due)
        except Exception as exc:
            print('Background job error:',type(exc).__name__)
        await asyncio.sleep(12)


@asynccontextmanager
async def lifespan(app:FastAPI):
    ensure_state()
    worker=asyncio.create_task(background_jobs())
    yield
    worker.cancel()
    try: await worker
    except asyncio.CancelledError: pass

app=FastAPI(title='Quillon LeadLoop 2.0',version='2.0',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')

# Optional shared-password boundary for public demonstration deployments.
# This is not enterprise identity; it prevents drive-by anonymous access to a demo
# where AD identity is deliberately selectable and actions are stored locally.
@app.middleware('http')
async def demo_site_access(request:Request, call_next):
    if request.url.path == '/healthz':
        return await call_next(request)
    password=os.getenv('LEADLOOP_SITE_PASSWORD','')
    if password:
        raw=request.headers.get('authorization','')
        authorized=False
        if raw.lower().startswith('basic '):
            try:
                pair=base64.b64decode(raw[6:].strip(),validate=True).decode('utf-8')
                name,received=pair.split(':',1)
                authorized=(hmac.compare_digest(name,os.getenv('LEADLOOP_SITE_USER','reviewer'))
                            & hmac.compare_digest(received,password))
            except (ValueError,UnicodeDecodeError):
                pass
        if not authorized:
            return Response('Access restricted to case reviewers.',status_code=401,
                            headers={'WWW-Authenticate':'Basic realm="LeadLoop Case Demo"'})
    return await call_next(request)

@app.get('/healthz')
def healthz():
    return {'status':'ok'}


class LoginInput(BaseModel):
    role: Literal['ad','manager']
    user_id: str | None = None
    pin: str | None = None

class ActionInput(BaseModel):
    mql_id: str
    action_type: Literal['Log outreach','Change status','Reassign','Flag data issue','Return to marketing']
    new_status: str | None = None
    assigned_to: str | None = None
    channel: str | None = None
    reason_code: str | None = None
    note: str = Field('',max_length=3000)

class AlertInput(BaseModel):
    mql_id: str

class AssignInput(BaseModel):
    mql_id: str
    assigned_to: str
    note: str = Field('Queue triage',max_length=3000)


@app.get('/')
def index(): return FileResponse(BASE/'static'/'index.html')

@app.get('/api/health')
def health(): return {'status':'ok','mode':'offline' if not slack_configured() else 'slack_configured'}

@app.get('/api/diagnosis')
def diagnosis():
    d,m=source();w=volume_waterfall(d,m)
    b=not_worked_breakdown(m,'Q3')
    deep=operating_breakdown(d,m,'Q3')
    q2=m[m.quarter=='Q2'];q3=m[m.quarter=='Q3']
    return {'metrics':{
        'q2_mql':w['q2_mql'],'q3_mql':w['q3_mql'],
        'q2_forms':w['q2_forms'],'q3_forms':w['q3_forms'],
        'q2_imports':w['q2_imports'],'q3_imports':w['q3_imports'],
        'missing_q2':w['missing_q2'],'missing_q3':w['missing_q3'],
        'missing_v2':w['missing_v2'],'other_unmatched':w['other_unmatched'],
        'worked_q2':int(q2.reported_worked.sum()),'worked_q3':int(q3.reported_worked.sum()),
        'q3_unassigned':int(q3.unassigned_at_creation.sum()),'q3_alert_failed':int(q3.alert_failed.sum()),
        'q3_late_system_conversions':int(q3.system_late_conversion.sum())},
        'acquisition': acquisition_source_evidence(d,m),
        'value_at_risk': pipeline.acquisition_value(d,m),
        'breakdown':as_records(b), 'operational':deep, 'team_comparison':team_comparison(d,m,'Q3'), 'qualification_backtest':qualification_backtest(d,m,'Q3'), 'guard':risk_checks(d,m),
        'hypotheses':[
          {'title':'CMS mappings broke high-intent capture','confidence':'High signal · root cause unverified','evidence':'48 Q3 demo/contact v2 forms lack a matching MQL event after the CMS migration.','falsify':'Check trigger logs, exclusions, deduplication and intentional suppression.'},
          {'title':'Delivery and routing friction block AD action','confidence':'High for defects, medium for causality','evidence':'70 initially unassigned leads and 61 failed notification records in Q3.','falsify':'Review delivery acknowledgments, subsequent reassignment and actual outreach.'},
          {'title':'Weak qualification ownership is a plausible adoption driver','confidence':'Hypothesis; SDR staffing history not in these files','evidence':'The team roster shows ADs and AEs; content and newsletter forms are MQL-eligible alongside demo requests. Existing customers can also reach AD queues.','falsify':'Review prior SDR roster, seller rejection reasons, meeting outcomes and independently measured lead quality.'},
          {'title':'Reported worked is not seller outreach','confidence':'High','evidence':'The report counts any status transition within SLA, even automation.','falsify':'Reconcile with seller activities and source timestamps.'}
        ]}


@app.get('/api/users')
def users():
    d,_=source();return {'ads':known_ads(d)}


@app.post('/api/login')
def login(request:LoginInput,response:Response):
    if request.role=='manager':
        if not request.pin or not hmac.compare_digest(request.pin,MANAGER_PIN):
            raise HTTPException(401,'Invalid manager PIN')
        user='MANAGER-DEMO'
    else:
        d,_=source()
        if request.user_id not in {u['user_id'] for u in known_ads(d)}:
            raise HTTPException(400,'Select an active AD')
        user=request.user_id
    response.set_cookie('ql_session',signed_session(user,request.role),httponly=True,samesite='lax',secure=os.getenv('LEADLOOP_SECURE_COOKIES','').lower() in ('1','true','yes'),max_age=8*3600)
    return {'user_id':user,'role':request.role}


@app.post('/api/logout')
def logout(response:Response):
    response.delete_cookie('ql_session');return {'ok':True}


@app.get('/api/session')
def session(request:Request):
    return {'session':read_session(request)}


@app.get('/api/queue')
def queue(request:Request,quarter:Literal['Q2','Q3','ALL']='Q3',state:Literal['all','needs_action','actioned']='needs_action',search:str='',lane:Literal['ALL','Hand-raisers','CS review','Aged / recycle review','Qualified fit','Nurture / low signal']='ALL'):
    session=require_user(request,'ad')
    d,m=source();merged=overlay(d,m)
    rows=queue_rows(merged,session['sub'],quarter,state,search[:100],lane)
    return {'rows':as_records(rows),'total':len(rows),'actor':session['sub']}


@app.get('/api/ad/metrics')
def ad_performance(request:Request,quarter:Literal['Q2','Q3','ALL']='Q3'):
    session=require_user(request,'ad')
    d,m=source();df=overlay(d,m)
    return ad_metrics(d,df,session['sub'],quarter)

class NextActionRequest(BaseModel):
    mql_id: str

@app.post('/api/ad/next-action')
def ad_next_action(request:Request,payload:NextActionRequest):
    session=require_user(request,'ad')
    if not ai_configured():raise HTTPException(503,'Claude is not configured. Explainable rule guidance remains available.')
    d,m=source();df=overlay(d,m)
    found=df[df.mql_id.eq(payload.mql_id)]
    if found.empty:raise HTTPException(404,'Unknown MQL')
    row=json_value(found.iloc[0].to_dict())
    if row['effective_owner']!=session['sub']:raise HTTPException(403,'Lead is not assigned to this AD')
    acct=d['accounts'][d['accounts'].account_id.eq(row.get('account_id'))]
    account=json_value(acct.iloc[0].to_dict()) if not acct.empty else {}
    baseline=next_action_rule(row,account)
    if baseline.get('blocked'):raise HTTPException(409,'Closed or manager-held lead: no new outreach suggested')
    if row.get('effective_status')=='Marketing Review' or row.get('qualification_approved') in ('Send to CS','Hold for enrichment','Return to nurture'):
        raise HTTPException(409,'Lead is held for Marketing Ops review; no outreach suggestion available')
    try:
        return {'advisory':ai_next_best_action(row,account,baseline)}
    except RuntimeError as exc:
        raise HTTPException(503,str(exc)) from exc

@app.get('/api/leads/{mql_id}')
def lead_detail(mql_id:str, request:Request):
    session=require_user(request)
    d,m=source();merged=overlay(d,m)
    found=merged[merged.mql_id==mql_id]
    if found.empty: raise HTTPException(404,'MQL not found')
    record=json_value(found.iloc[0].to_dict())
    if session['role']!='manager' and record['effective_owner']!=session['sub']:
        raise HTTPException(403,'Lead is not assigned to this AD')
    contact=record.get('contact_id')
    items={}
    for key,source_df,filter_key,date_key,cols in [
        ('status_history',d['mql_status_history'],'mql_id','changed_at',['changed_at','old_status','new_status','changed_by','note']),
        ('routing',d['routing_log'],'mql_id',None,None),
        ('notifications',d['notifications_log'],'mql_id','sent_at',None),
        ('activities',d['ad_activity'],'contact_id','timestamp',None)]:
        keydf=source_df[source_df[filter_key].astype(str)==str(mql_id if filter_key=='mql_id' else contact)].copy()
        if date_key and date_key in keydf.columns: keydf=keydf.sort_values(date_key,ascending=False)
        # Activity is contact-scoped. Only present after this event before next MQL.
        if key=='activities' and len(keydf):
            ts=pd.to_datetime(keydf.timestamp,utc=True)
            start=pd.to_datetime(record['mql_timestamp'],utc=True)
            end=pd.to_datetime(record['next_mql_at'],utc=True) if record.get('next_mql_at') else None
            keydf=keydf[(ts>=start)&((ts<end) if end is not None else True)]
        if cols: keydf=keydf[cols]
        items[key]=as_records(keydf.head(25))
    acts=get_actions(DB)
    items['local_actions']=as_records(acts[acts.mql_id==mql_id].head(40))
    acct=d['accounts'][d['accounts'].account_id.eq(record.get('account_id'))]
    items['account']=json_value(acct.iloc[0].to_dict()) if not acct.empty else None
    contacts=d['contacts'][d['contacts'].contact_id.eq(contact)]
    items['contact']=json_value(contacts.iloc[0].to_dict()) if not contacts.empty else None
    form=d['form_submissions'][d['form_submissions'].submission_id.eq(record.get('trigger_submission_id'))]
    items['submission']=json_value(form.iloc[0].to_dict()) if not form.empty else None
    opps=d['opportunities'][(d['opportunities'].contact_id.eq(contact)) | (d['opportunities'].account_id.eq(record.get('account_id')))].copy()
    items['opportunities']=as_records(opps.head(25))
    with state_connection() as conn:
        reviews=qualification_list(found,conn)
    items['qualification_review']=reviews[0]['review'] if reviews else None
    return {'lead':record,'related':items,'journey':journey_for_lead(record,items),'next_action':next_action_rule(record,items['account'],items['activities'])}


@app.post('/api/actions')
def action(request:Request,payload:ActionInput):
    session=require_user(request,'ad')
    d,m=source();merged=overlay(d,m)
    row=merged[merged.mql_id==payload.mql_id]
    if row.empty: raise HTTPException(404,'Unknown MQL')
    if row.iloc[0].effective_owner!=session['sub']:
        raise HTTPException(403,'Cannot modify another AD’s lead')
    if row.iloc[0].qualification_approved in ('Send to CS','Hold for enrichment','Return to nurture'):
        raise HTTPException(409,'Marketing Ops has placed this lead outside the AD queue; ask the manager to re-approve Send to AD')
    active_users=known_ads(d)
    users={u['user_id'] for u in active_users}
    if payload.action_type=='Reassign':
        if payload.assigned_to==session['sub']:
            raise HTTPException(422,'Choose another AD; this lead is already assigned to you')
        # Missing CRM territory is not a reason to allow an unrestricted cross-region move.
        # Use the signed-in owner's region as a provisional restriction, with a mandatory note.
        original_region=row.iloc[0].region
        missing_region=pd.isna(original_region) or not str(original_region).strip()
        actor_region=next((u['region'] for u in active_users if u['user_id']==session['sub']),None)
        eligible_region=actor_region if missing_region else original_region
        if not any(u['user_id']==payload.assigned_to and u['region']==eligible_region for u in active_users):
            raise HTTPException(422,'Choose another active AD in the same region; territory exceptions require management review')
        if missing_region and not payload.note.strip():
            raise HTTPException(422,'Add a note explaining a reassignment when the lead region is missing')
    try:
        with APP_LOCK:
            if row.iloc[0].effective_status=='Marketing Review':
                raise HTTPException(409,'Lead is awaiting Marketing review; the manager must release it first')
            with state_connection() as conn:
                reason_codes=get_rejection_reasons(conn)
            action_id=add_action(DB,payload.mql_id,session['sub'],payload.action_type,payload.note,set(m.mql_id),
                       new_status=payload.new_status,assigned_to=payload.assigned_to,channel=payload.channel,
                       valid_users=users,reason_code=payload.reason_code,
                       valid_reason_codes=reason_codes)
            if payload.action_type=='Reassign':
                notify_row=json_value(row.iloc[0].to_dict())
                notify_row['effective_owner']=payload.assigned_to
                notify_row['region']=next(u['region'] for u in active_users if u['user_id']==payload.assigned_to)
                with state_connection() as conn:
                    insert_notification(conn,notify_row,reason=f'reassigned:{action_id}')
                    conn.commit()
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    return {'ok':True,'actor':session['sub'],'message':'Action recorded locally and visible to marketing. Salesforce not mutated.'}


class ResumeMarketingInput(BaseModel):
    mql_id: str
    note: str = Field(...,min_length=5,max_length=3000)

@app.post('/api/manager/return-to-ad')
def return_to_ad(payload:ResumeMarketingInput,request:Request):
    require_user(request,'manager')
    d,m=source();df=overlay(d,m)
    found=df[df.mql_id==payload.mql_id]
    if found.empty: raise HTTPException(404,'Unknown lead')
    record=found.iloc[0]
    if record.effective_status!='Marketing Review':
        raise HTTPException(409,'Lead does not have an open Marketing review')
    if record.effective_owner not in {u['user_id'] for u in known_ads(d)}:
        raise HTTPException(409,'Assign an active owner before returning this lead to sales')
    with APP_LOCK:
        action_id=add_action(DB,payload.mql_id,'MANAGER-DEMO','Return to AD',payload.note,set(m.mql_id))
        row=json_value(record.to_dict())
        with state_connection() as conn:
            insert_notification(conn,row,reason=f'marketing-return:{action_id}')
            conn.commit()
    return {'ok':True,'message':'Returned to assigned AD; Slack notification queued in outbox'}

@app.get('/api/manager')
def manager(request:Request,quarter:Literal['Q2','Q3','ALL']='Q3'):
    require_user(request,'manager')
    d,m=source();df=overlay(d,m)
    if quarter!='ALL':df=df[df.quarter.eq(quarter)]
    actions=get_actions(DB)
    current_counts=df.effective_status.fillna('Unknown').value_counts()
    deep=operating_breakdown(d,m,'Q3')
    return {
        'operational':deep, 'team_comparison':team_comparison(d,m,'Q3'), 'data_checks':risk_checks(d,m),
        'counts':{'total':len(df),'worked_historical':int(df.reported_worked.sum()),
                  'without_owner':int(df.effective_owner.fillna('SYS-QUEUE').eq('SYS-QUEUE').sum()),
                  'local_actions':int(df.demo_action_count.sum()),
                  'needs_action':int(df.needs_ad_action.sum()),
                  'marketing_review':int(df.effective_status.eq('Marketing Review').sum())},
        'status_distribution':[{'status':s,'count':int(n)} for s,n in current_counts.items()],
        'by_region':as_records(df.groupby('region',dropna=False).agg(mqls=('mql_id','size'),worked=('reported_worked','sum'),actions=('demo_action_count','sum')).reset_index()),
        'by_owner':as_records(df.groupby('effective_owner',dropna=False).agg(mqls=('mql_id','size'),worked=('reported_worked','sum'),actions=('demo_action_count','sum')).reset_index().sort_values('mqls',ascending=False)),
        'actions':as_records(actions.head(100)),
        'returned_to_marketing':as_records(df[df.effective_status.eq('Marketing Review')][['mql_id','contact_name','account_name','effective_owner','queue_score']].sort_values('queue_score',ascending=False).head(30)),
        'unassigned':as_records(df[df.effective_owner.fillna('SYS-QUEUE').eq('SYS-QUEUE')][['mql_id','contact_name','account_name','region','form_kind','priority','queue_score']].sort_values('queue_score',ascending=False).head(10)),
        'ads':known_ads(d),
        'outbox':notification_log(limit=40),
        'integrations':{'slack_mode':'live' if slack_configured() else 'simulation','sf_mode':'scaffold only / CSV source'}
    }


def notification_log(limit=50):
    with state_connection() as conn:
        return [dict(r) for r in conn.execute('''SELECT id,event_key,mql_id,channel,state,attempts,last_error,external_id,created_at,processed_at FROM notification_outbox ORDER BY id DESC LIMIT ?''',(limit,))]


@app.post('/api/manager/assign')
def manager_assign(payload:AssignInput,request:Request):
    require_user(request,'manager')
    d,m=source();merged=overlay(d,m)
    found=merged[merged.mql_id.eq(payload.mql_id)]
    if found.empty: raise HTTPException(404,'Unknown MQL')
    current=found.iloc[0].effective_owner
    if pd.notna(current) and current!='SYS-QUEUE':
        raise HTTPException(409,'Lead already assigned to an AD; refresh the queue')
    active_users=known_ads(d)
    users={u['user_id'] for u in active_users}
    if payload.assigned_to not in users: raise HTTPException(422,'Choose a currently active AD')
    if pd.notna(found.iloc[0].region) and not any(u['user_id']==payload.assigned_to and u['region']==found.iloc[0].region for u in active_users):
        raise HTTPException(422,'Choose an active AD in the same region; territory exceptions require review')
    if not payload.note.strip(): raise HTTPException(422,'Provide an assignment reason')
    with APP_LOCK:
        action_id=add_action(DB,payload.mql_id,'MANAGER-DEMO','Reassign',payload.note,set(m.mql_id),
                             assigned_to=payload.assigned_to,valid_users=users)
        notify_row=json_value(found.iloc[0].to_dict())
        notify_row['effective_owner']=payload.assigned_to
        notify_row['region']=next(u['region'] for u in active_users if u['user_id']==payload.assigned_to)
        with state_connection() as conn:
            insert_notification(conn,notify_row,reason=f'manager-reassigned:{action_id}')
            conn.commit()
    return {'ok':True,'assigned_to':payload.assigned_to,'notification_queued':True}


@app.post('/api/manager/test-alert')
def test_alert(payload:AlertInput,request:Request):
    require_user(request,'manager')
    d,m=source();merged=overlay(d,m)
    rows=merged[merged.mql_id==payload.mql_id]
    if rows.empty: raise HTTPException(404,'Unknown MQL')
    with state_connection() as conn:
        # Each manual test gets its own unique key, only on explicit manager request.
        reason='manual-'+secrets.token_hex(5)
        result=insert_notification(conn,json_value(rows.iloc[0].to_dict()),reason)
        conn.commit()
    return {'queued':result,'mode':'live' if slack_configured() else 'simulation','events':notification_log(5)}


@app.post('/api/manager/run-alerts')
def run_alerts(request:Request):
    require_user(request,'manager')
    return {'processed':deliver_due(),'events':notification_log(40)}


@app.get('/api/manager/export')
def export_actions(request:Request):
    require_user(request,'manager')
    df=get_actions(DB)
    output=io.StringIO()
    df.to_csv(output,index=False)
    raw=output.getvalue().encode('utf-8-sig')
    return Response(raw,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="quillon_marketing_feedback.csv"'})


if __name__=='__main__':
    import uvicorn
    uvicorn.run('server:app',host='127.0.0.1',port=APP_PORT,reload=False)


# ---- Marketing Ops-owned rules and qualification workbench ----
class RulesInput(BaseModel):
    weights: dict[str,int]
    reason: str = Field(min_length=4,max_length=3000)

class ReviewInput(BaseModel):
    mql_id: str
    decision: Literal['Send to AD','Send to CS','Hold for enrichment','Return to nurture']
    reason: str = Field(min_length=4,max_length=3000)

@app.get('/api/reason-codes')
def reason_codes_for_ad(request:Request):
    require_user(request,'ad')
    with state_connection() as conn: return {'codes':get_rejection_reasons(conn)}

class ReasonConfigInput(BaseModel):
    codes: list[str]
    justification: str = Field(min_length=5,max_length=1000)

@app.post('/api/manager/reason-codes')
def update_reason_codes(payload:ReasonConfigInput,request:Request):
    require_user(request,'manager')
    try:
        with APP_LOCK, state_connection() as conn:
            set_rejection_reasons(conn,payload.codes,payload.justification)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    return {'ok':True,'codes':payload.codes}

@app.post('/api/manager/reason-codes/undo')
def undo_reason_codes(request:Request):
    require_user(request,'manager')
    with APP_LOCK, state_connection() as conn:
        codes=undo_rejection_reasons(conn)
    return {'ok':True,'codes':codes}

@app.get('/api/manager/rules')
def get_rules(request:Request):
    require_user(request,'manager')
    with state_connection() as conn:
        weights=scoring_config(conn)
        history=[dict(x) for x in conn.execute('SELECT id,changed_at,actor,reason,weights_json FROM scoring_config ORDER BY id DESC LIMIT 10')]
        reasons=get_rejection_reasons(conn)
        reason_history=[dict(x) for x in conn.execute('SELECT id,changed_at,actor,reason,reasons_json FROM reason_config ORDER BY id DESC LIMIT 5')]
    return {'reason_codes':reasons,'reason_history':reason_history,'weights':weights, 'bounds':{k:list(v) for k,v in BOUNDS.items()}, 'history':history,
            'principle':'Human-editable ranking points, not conversion probabilities. Changes are audited and reversible.'}

@app.post('/api/manager/rules')
def change_rules(payload:RulesInput,request:Request):
    require_user(request,'manager')
    try:
        with APP_LOCK, state_connection() as conn:
            set_config(conn,payload.weights,payload.reason)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    return {'ok':True,'weights':payload.weights}

@app.post('/api/manager/rules/undo')
def revert_rules(request:Request):
    require_user(request,'manager')
    with APP_LOCK, state_connection() as conn:
        undo_config(conn)
        values=scoring_config(conn)
    return {'ok':True,'weights':values}

@app.get('/api/manager/data-guard')
def data_guard(request:Request):
    require_user(request,'manager')
    d,m=source()
    return {'checks':risk_checks(d,m),'mode':'read-only checks; remediation requires evidence and approval'}

@app.get('/api/manager/qualification')
def qualification_queue(request:Request,quarter:Literal['Q2','Q3','ALL']='Q3'):
    require_user(request,'manager')
    d,m=source()
    if quarter!='ALL':m=m[m.quarter==quarter]
    with state_connection() as conn:
        rows=qualification_list(m,conn)
    pending=[r for r in rows if not r['review'] and not r['control']]
    return {'rows':rows[:240], 'pending':len(pending),'control':sum(r['control'] for r in rows),
            'backtest':qualification_backtest(d,m,quarter if quarter!='ALL' else 'Q3'),
            'hypothesis':'No pre-AD qualification may increase workload; not established as causal.',
            'policy':'No automatic rejection. Hand-raisers bypass. Existing customers flagged for CS review. Missing accounts held only after human approval. Stable ~20% experimental holdout has no automatic rerouting.',
            'decisions':sorted(VALID_DECISIONS),'ai_available':ai_configured()}

@app.post('/api/manager/qualification/review')
def review_qualification(payload:ReviewInput,request:Request):
    session=require_user(request,'manager')
    d,m=source();found=m[m.mql_id.eq(payload.mql_id)]
    if found.empty:raise HTTPException(404,'Unknown MQL')
    suggestion=found.iloc[0].qualification_suggestion
    if control_group(payload.mql_id):
        raise HTTPException(409,'Control-group MQL: prequalification treatment is disabled to preserve the comparison')
    # High-intent leads cannot be silently discarded or held.
    if found.iloc[0].form_kind in {'demo','partner','contact'} and payload.decision!='Send to AD':
        raise HTTPException(422,'Hand-raiser must remain AD-routed; escalate exceptional cases separately')
    with APP_LOCK, state_connection() as conn:
        record_review(conn,payload.mql_id,session['sub'],payload.decision,payload.reason,suggestion)
    return {'ok':True,'decision':payload.decision,'mql_id':payload.mql_id,
            'note':'Local approval ledger only; Salesforce and CS tasks require live integration.'}


@app.post('/api/manager/qualification/ai-suggest')
def ai_suggest(payload:AlertInput,request:Request):
    require_user(request,'manager')
    d,m=source();found=m[m.mql_id.eq(payload.mql_id)]
    if found.empty:raise HTTPException(404,'Unknown MQL')
    row=found.iloc[0]
    values={key:json_value(row.get(key)) for key in
            ('form_kind','fit_tier','customer_status','seniority','title','segment','region','priority')}
    try:return {'mql_id':payload.mql_id,'advisory':ai_recommend(values)}
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from exc


@app.get('/api/manager/qualification/export')
def qualification_export(request:Request):
    require_user(request,'manager')
    with state_connection() as conn:
        setup_advanced(conn)
        rows=pd.read_sql_query('SELECT id,created_at,mql_id,actor,decision,reason,suggestion_json FROM qualification_reviews ORDER BY id',conn)
    stream=io.StringIO();rows.to_csv(stream,index=False)
    return Response(stream.getvalue().encode('utf-8-sig'),media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="quillon_qualification_audit.csv"'})


# ---------------------------------------------------------------------------
# LeadLoop pipeline: run the end-to-end workflow over the raw form stream,
# keep the latest run, and let ADs act on the lists it builds.

class PipelineRunInput(BaseModel):
    ai: Literal['auto','offline']='auto'

class PipelineDecisionInput(BaseModel):
    submission_id: str
    decision: Literal['accept','reject','return']
    reason_code: str | None = None
    note: str = Field('',max_length=2000)
    via: Literal['workspace','slack']='workspace'


def latest_run():
    with state_connection() as conn:
        row=conn.execute('SELECT id,summary_json,traces_json FROM pipeline_runs ORDER BY id DESC LIMIT 1').fetchone()
    if not row:return None,None,None
    return row['id'],json.loads(row['summary_json']),json.loads(row['traces_json'])


def pipeline_decisions(run_id):
    with state_connection() as conn:
        # Decisions belong to the lead, so they survive a re-run of the pipeline.
        rows=conn.execute('''SELECT * FROM pipeline_decisions WHERE id IN
            (SELECT MAX(id) FROM pipeline_decisions GROUP BY submission_id)''').fetchall()
    return {r['submission_id']:dict(r) for r in rows}


def pipeline_feedback_summary(run_id,summary):
    decs=list(pipeline_decisions(run_id).values())
    leads={l['sid']:l for l in summary['leads']}
    by_reason={};by_form={}
    for x in decs:
        if x['reason_code']:by_reason[x['reason_code']]=by_reason.get(x['reason_code'],0)+1
        f=leads.get(x['submission_id'],{}).get('form','?')
        by_form.setdefault(f,{'accept':0,'reject':0,'return':0})[x['decision']]+=1
    return {'total':len(decs),'accept':sum(x['decision']=='accept' for x in decs),'reject':sum(x['decision']=='reject' for x in decs),
            'return':sum(x['decision']=='return' for x in decs),'by_reason':by_reason,'by_form':by_form,
            'recent':sorted(decs,key=lambda x:x['id'],reverse=True)[:12]}


def with_context(run_id,summary):
    d,m=source()
    summary['run_id']=run_id
    summary['value']=pipeline.acquisition_value(d,m)
    summary['feedback']=pipeline_feedback_summary(run_id,summary)
    return summary


@app.get('/api/pipeline/stages')
def pipeline_stages():
    return {'stages':pipeline.STAGES,'executors':pipeline.EXEC,'ai_configured':ai_configured()}


@app.post('/api/pipeline/run')
def pipeline_run(payload:PipelineRunInput):
    d,m=source()
    with state_connection() as conn:
        weights=scoring_config(conn)
        cache={r['submission_id']:json.loads(r['result_json']) for r in conn.execute('SELECT * FROM pipeline_ai_cache')}
    before=set(cache)
    summary,traces=pipeline.run(d,m,weights,payload.ai,'Q3',cache)
    with APP_LOCK, state_connection() as conn:
        conn.executemany('INSERT OR REPLACE INTO pipeline_ai_cache(submission_id,result_json) VALUES(?,?)',
                         [(k,json.dumps(v)) for k,v in cache.items() if k not in before])
        cur=conn.execute('INSERT INTO pipeline_runs(ran_at,summary_json,traces_json) VALUES(?,?,?)',
                         (summary['ran_at'],json.dumps(summary,default=str),json.dumps(traces,default=str)))
        conn.commit()
        run_id=cur.lastrowid
    return with_context(run_id,summary)


@app.get('/api/pipeline/latest')
def pipeline_latest():
    run_id,summary,_=latest_run()
    return {'run':with_context(run_id,summary) if summary else None}


@app.get('/api/pipeline/trace/{submission_id}')
def pipeline_trace(submission_id:str):
    run_id,summary,traces=latest_run()
    if not traces or submission_id not in traces:raise HTTPException(404,'Lead not in the latest run')
    lead=next(l for l in summary['leads'] if l['sid']==submission_id)
    steps=list(traces[submission_id])
    dec=pipeline_decisions(run_id).get(submission_id)
    if dec:
        label={'accept':'Accepted','reject':'Rejected','return':'Returned to marketing'}[dec['decision']]
        steps.append({'stage':'feedback','executor':'human','status':'ok',
                      'title':f"{label} by {dec['actor']} via {dec['via']}",
                      'detail':' · '.join(x for x in [dec.get('reason_code'),dec.get('note')] if x) or 'No note.'})
    return {'lead':lead,'steps':steps,'decision':dec}


@app.get('/api/pipeline/inbox')
def pipeline_inbox(request:Request):
    session=require_user(request,'ad')
    run_id,summary,_=latest_run()
    if not summary:return {'run':None,'rows':[]}
    decs=pipeline_decisions(run_id)
    rows=[dict(l,decision=decs.get(l['sid'])) for l in summary['leads'] if l['owner']==session['sub'] and l['outcome'] in ('AD inbox','CS handoff')]
    rows.sort(key=lambda l:(l['decision'] is not None,not l['hand'],-(l['score'] or 0)))
    return {'run':{'run_id':run_id,'ran_at':summary['ran_at'],'ai_mode':summary['ai_mode']},'rows':rows}


@app.post('/api/pipeline/decision')
def pipeline_decision(payload:PipelineDecisionInput,request:Request):
    run_id,summary,_=latest_run()
    if not summary:raise HTTPException(409,'Run the pipeline first')
    lead=next((l for l in summary['leads'] if l['sid']==payload.submission_id),None)
    if not lead or not lead['owner']:raise HTTPException(404,'Lead is not in an AD list')
    if payload.via=='slack':
        # Demo Slack buttons act as the routed AD; in production Slack signs the clicking user.
        actor=lead['owner']
    else:
        session=require_user(request,'ad')
        if lead['owner']!=session['sub']:raise HTTPException(403,'Lead is routed to another AD')
        actor=session['sub']
    with state_connection() as conn:
        codes=get_rejection_reasons(conn)
        reason=payload.reason_code if payload.decision in ('reject','return') else None
        if payload.decision in ('reject','return') and reason not in codes:raise HTTPException(422,'Choose a reason from the current list')
        conn.execute('INSERT INTO pipeline_decisions(run_id,submission_id,actor,decision,reason_code,note,via) VALUES(?,?,?,?,?,?,?)',
                     (run_id,payload.submission_id,actor,payload.decision,reason,payload.note.strip(),payload.via))
        conn.commit()
    return {'ok':True,'actor':actor,'feedback':pipeline_feedback_summary(run_id,summary)}
