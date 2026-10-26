from pathlib import Path
import os
import sqlite3
import json
import pytest
from fastapi.testclient import TestClient
import server
from integration import SlackAdapter, DeliveryError, SalesforceAdapter, resolve_channel, slack_payload

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, 'DB', tmp_path/'test.sqlite')
    monkeypatch.setattr(server, 'MANAGER_PIN','unit-test-manager-pin')
    monkeypatch.delenv('SLACK_BOT_TOKEN',raising=False)
    # Use lifespan; initial legacy baseline is marked seen and not emitted to Slack.
    with TestClient(server.app) as cl:
        yield cl


def sign_in_ad(cl):
    ads=cl.get('/api/users').json()['ads']
    assert ads
    response=cl.post('/api/login',json={'role':'ad','user_id':ads[0]['user_id']})
    assert response.status_code==200,response.text
    return ads[0]['user_id']


def sign_in_manager(cl):
    response=cl.post('/api/login',json={'role':'manager','pin':'unit-test-manager-pin'})
    assert response.status_code==200,response.text


def test_metrics_and_guided_ux(client):
    r=client.get('/api/diagnosis')
    assert r.status_code==200
    m=r.json()['metrics']
    assert (m['q2_mql'],m['q3_mql'],m['missing_q3'],m['missing_v2'])==(640,467,80,48)
    assert len(r.json()['breakdown'])==5
    assert client.get('/').status_code==200
    assert 'investigation' in client.get('/').text.lower()
    js=client.get('/static/app.js').text
    assert 'data-lead' in js and 'revealBridge' in js and 'managerPIN' in js


def test_actor_not_provided_and_queue_click_flow(client):
    assert client.get('/api/queue').status_code==401
    actor=sign_in_ad(client)
    queue=client.get('/api/queue?quarter=Q3&state=all').json()
    assert queue['actor']==actor
    assert queue['rows']
    assert queue['rows'][0]['queue_score']>=queue['rows'][-1]['queue_score']
    one=queue['rows'][0]
    detail=client.get('/api/leads/'+one['mql_id'])
    assert detail.status_code==200
    assert 'opportunities' in detail.json()['related']
    assert 'status_history' in detail.json()['related']
    # Actor cannot be injected via request body or impersonate another AD's lead.
    bad=client.post('/api/actions',json={'mql_id':one['mql_id'],'actor':'MANAGER-DEMO','action_type':'Change status','new_status':'Disqualified','note':''})
    assert bad.status_code==422
    before=client.post('/api/actions',json={'mql_id':one['mql_id'],'action_type':'Change status','new_status':'Disqualified','note':''})
    assert before.status_code==422
    flag=client.post('/api/actions',json={'mql_id':one['mql_id'],'action_type':'Flag data issue','note':''})
    assert flag.status_code==422
    saved=client.post('/api/actions',json={'mql_id':one['mql_id'],'action_type':'Change status','new_status':'Disqualified','note':'Duplicate inquiry','reason_code':'Duplicate / already engaged'})
    assert saved.status_code==200,saved.text
    with sqlite3.connect(server.DB) as conn:
        actions=conn.execute('SELECT actor,new_status,note FROM actions').fetchall()
    assert actions==[(actor,'Disqualified','Duplicate inquiry')]
    after=client.get('/api/queue?quarter=Q3&state=all').json()['rows']
    changed=next(x for x in after if x['mql_id']==one['mql_id'])
    assert changed['effective_status']=='Disqualified'
    assert changed['demo_action_count']==1
    other=next((x for x in server.source()[1].mql_id if x not in {r['mql_id'] for r in after}),None)
    assert client.get('/api/leads/'+other).status_code==403
    assert client.post('/api/actions',json={'mql_id':other,'action_type':'Flag data issue','note':'Check'}).status_code==403


def test_manager_auth_dashboard_export_and_simulated_slack(client):
    assert client.get('/api/manager').status_code==401
    assert client.post('/api/login',json={'role':'manager','pin':'bad'}).status_code==401
    sign_in_manager(client)
    dashboard=client.get('/api/manager?quarter=Q3')
    assert dashboard.status_code==200
    assert dashboard.json()['counts']['total']==467
    q3_ids=server.source()[1].query("quarter == 'Q3'").mql_id
    alert=client.post('/api/manager/test-alert',json={'mql_id':str(q3_ids.iloc[0])})
    assert alert.status_code==200
    processed=client.post('/api/manager/run-alerts')
    assert processed.status_code==200
    logs=client.get('/api/manager').json()['outbox']
    assert logs[0]['state']=='simulated' and logs[0]['attempts']==1
    assert client.post('/api/manager/test-alert',json={'mql_id':'bad'}).status_code==404
    csv=client.get('/api/manager/export')
    assert csv.status_code==200
    assert 'text/csv' in csv.headers['content-type']
    assert 'actor' in csv.text
    assert client.get('/api/queue').status_code==403


def test_historical_events_do_not_send_at_bootstrap(client):
    with sqlite3.connect(server.DB) as conn:
        assert conn.execute('SELECT COUNT(*) FROM seen_mqls').fetchone()[0]==1107
        assert conn.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0]==0


def test_salesforce_scaffold_pagination(monkeypatch):
    sf=SalesforceAdapter('https://mydomain.my.salesforce.com','dummy',api_version='v61.0')
    calls=[]
    def fake(method,path,params=None,json_payload=None):
        calls.append((method,path,params))
        if len(calls)==1:return {'records':[{'Id':'001'}], 'nextRecordsUrl':'/services/data/v61.0/query/next', 'done':False}
        return {'records':[{'Id':'002'}], 'done':True}
    monkeypatch.setattr(sf,'request',fake)
    assert len(sf.query_all('SELECT Id FROM Lead'))==2
    assert calls[1][1]=='/services/data/v61.0/query/next'
    with pytest.raises(ValueError): sf.query_all('DELETE Lead')
    with pytest.raises(ValueError): sf.patch_record('Lead','bad',{'Status':'Working'})


def test_slack_simulation_without_network():
    slack=SlackAdapter(token='')
    assert slack.post({'channel':'#mql-na','text':'test'}).status=='simulated'
    payload=slack_payload({'mql_id':'M00001','contact_name':'Test','effective_owner':'AD-NA-01','region':'NA'})
    assert payload['channel']=='#mql-na'
    assert 'M00001' in payload['text']


def test_manager_can_rescue_unassigned_and_queue_notification(client):
    sign_in_manager(client)
    dashboard=client.get('/api/manager?quarter=Q3').json()
    assert dashboard['counts']['without_owner']==70
    lead=dashboard['unassigned'][0]
    actor=next(u['user_id'] for u in dashboard['ads'] if not lead['region'] or u['region']==lead['region'])
    result=client.post('/api/manager/assign',json={'mql_id':lead['mql_id'],'assigned_to':actor,'note':'Assignment repaired'})
    assert result.status_code==200,result.text
    assert result.json()['notification_queued']
    dashboard2=client.get('/api/manager?quarter=Q3').json()
    assert dashboard2['counts']['without_owner']==69
    assert any(x['mql_id']==lead['mql_id'] and x['action_type']=='Reassign' for x in dashboard2['actions'])
    event=next(x for x in dashboard2['outbox'] if x['mql_id']==lead['mql_id'])
    assert event['state'] in ('pending','simulated')
    assert client.post('/api/manager/assign',json={'mql_id':lead['mql_id'],'assigned_to':actor}).status_code==409


def test_slack_retry_logic_429(monkeypatch):
    from integration import SlackAdapter, DeliveryError
    class Response:
        status_code=429
        headers={'Retry-After':'42'}
    monkeypatch.setattr('integration.requests.post', lambda *args,**kwargs: Response())
    with pytest.raises(DeliveryError) as exc:
        SlackAdapter(token='xoxb-fake').post({'channel':'C123','text':'test'})
    assert exc.value.retry_after==42
    assert not exc.value.permanent


def test_reassign_to_another_ad_visible_in_new_owners_default_queue(client):
    owner='AD-LATAM-02'
    destination='AD-LATAM-01'
    sign_in=client.post('/api/login',json={'role':'ad','user_id':owner})
    assert sign_in.status_code==200
    all_leads=client.get('/api/queue?quarter=Q3&state=all').json()['rows']
    lead=next(l for l in all_leads if l['region']=='LATAM' and l['effective_status']=='MQL')
    mql=lead['mql_id']

    missing=client.post('/api/actions',json={'mql_id':mql,'action_type':'Reassign'})
    assert missing.status_code==422
    same=client.post('/api/actions',json={'mql_id':mql,'action_type':'Reassign','assigned_to':owner})
    assert same.status_code==422
    wrong_region=client.post('/api/actions',json={'mql_id':mql,'action_type':'Reassign','assigned_to':'AD-NA-01'})
    assert wrong_region.status_code==422

    saved=client.post('/api/actions',json={'mql_id':mql,'action_type':'Reassign','assigned_to':destination,'note':'Transfer to teammate for timely response'})
    assert saved.status_code==200,saved.text
    assert mql not in [l['mql_id'] for l in client.get('/api/queue?quarter=Q3&state=all').json()['rows']]
    assert client.get('/api/leads/'+mql).status_code==403

    client.post('/api/login',json={'role':'ad','user_id':destination})
    default_queue=client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows']
    assert mql in [l['mql_id'] for l in default_queue], 'New owner must see reassigned lead by default'
    updated=next(l for l in default_queue if l['mql_id']==mql)
    assert updated['needs_ad_action'] is True
    assert updated['demo_action_count']==1
    assert client.get('/api/leads/'+mql).status_code==200

    contact=client.post('/api/actions',json={'mql_id':mql,'action_type':'Log outreach','channel':'Email','note':'First email sent'})
    assert contact.status_code==200
    assert mql not in [l['mql_id'] for l in client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows']]
    assert mql in [l['mql_id'] for l in client.get('/api/queue?quarter=Q3&state=actioned').json()['rows']]

    with sqlite3.connect(server.DB) as conn:
        events=conn.execute('SELECT actor, action_type, assigned_to FROM actions WHERE mql_id=? ORDER BY id',(mql,)).fetchall()
        outbox=conn.execute('SELECT state FROM notification_outbox WHERE mql_id=?',(mql,)).fetchall()
    assert events == [(owner,'Reassign',destination),(destination,'Log outreach',None)]
    assert outbox, 'Transfer must enqueue the Slack notification'


def test_reassignment_with_unverified_region_requires_explanation(client):
    owner='AD-LATAM-02'
    destination='AD-LATAM-03'
    assert client.post('/api/login',json={'role':'ad','user_id':owner}).status_code==200
    leads=client.get('/api/queue?quarter=Q2&state=all').json()['rows']
    lead=next(l for l in leads if l['region'] is None and l['effective_status']=='MQL')
    payload={'mql_id':lead['mql_id'],'action_type':'Reassign','assigned_to':destination}
    assert client.post('/api/actions',json=payload).status_code==422
    payload['note']='Account is within my LATAM territory; manager should confirm mapping.'
    saved=client.post('/api/actions',json=payload)
    assert saved.status_code==200,saved.text
    assert client.post('/api/login',json={'role':'ad','user_id':destination}).status_code==200
    rows=client.get('/api/queue?quarter=Q2&state=needs_action').json()['rows']
    assert lead['mql_id'] in [r['mql_id'] for r in rows]
