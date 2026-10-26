"""AD experience: tested on the supplied CSVs and with isolated external services."""
import json
import pytest
from fastapi.testclient import TestClient
import server
import ai_assist
from ad_experience import ad_metrics, journey_for_lead, next_action_rule

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'leadloop.sqlite')
    with TestClient(server.app) as c:yield c

def login_ad(c):
    u=c.get('/api/users').json()['ads'][0]
    assert c.post('/api/login',json={'role':'ad','user_id':u['user_id']}).status_code==200
    return u

def test_metrics_not_available_unauthorized(client):
    assert client.get('/api/ad/metrics').status_code==401
    assert client.post('/api/ad/next-action',json={'mql_id':'M00001'}).status_code==401

def test_metrics_reproducible_and_scoped(client):
    actor=login_ad(client)
    data=client.get('/api/ad/metrics?quarter=Q3')
    assert data.status_code==200,data.text
    result=data.json()
    assert result['actor']==actor['user_id'] and result['as_of']=='2026-10-08'
    assert result['total']>=0
    assert result['within_sla_own_activity']<=result['with_own_activity']<=result['total']
    assert sum(x['received'] for x in result['trend'])==result['total']
    assert sum(x['mqls'] for x in result['mix'])<=result['total']
    assert result['local_action_records']==0
    total=client.get('/api/ad/metrics?quarter=ALL').json()
    assert total['total']>=result['total']
    detail=client.get('/api/queue?quarter=Q3&state=all').json()['rows'][0]
    d=client.get('/api/leads/'+detail['mql_id']).json()
    assert len(d['journey'])==7
    assert d['next_action']['source']=='explainable_rule'
    assert d['next_action']['requires_human_approval'] is True
    assert d['next_action']['action_type'] in {'Log outreach','Flag data issue','Return to marketing'}
    assert not d['next_action']['email_draft']

def test_customer_or_bad_account_never_auto_contacts():
    r=next_action_rule({'form_kind':'newsletter','account_id':'A1','email':'x@y.tld'},{'customer_status':'Customer'})
    assert r['action_type']=='Return to marketing'
    r=next_action_rule({'form_kind':'gated_report','account_id':None,'email':'x@y.tld'})
    assert r['action_type']=='Flag data issue'
    r=next_action_rule({'form_kind':'demo','account_id':None,'email':'x@y.tld'})
    assert r['action_type']=='Log outreach' and r['channel']=='Email'

def test_ai_endpoint_permissions_and_guardrails(client,monkeypatch):
    u=login_ad(client)
    lead=client.get('/api/queue?quarter=Q3&state=all').json()['rows'][0]
    monkeypatch.setattr(server,'ai_configured',lambda:True)
    def fake_ai(row,acct,baseline):
        return {'source':'claude_advisory_only','action_type':baseline['action_type'],'title':'Human-reviewed next action','requires_human_approval':True}
    monkeypatch.setattr(server,'ai_next_best_action',fake_ai)
    result=client.post('/api/ad/next-action',json={'mql_id':lead['mql_id']})
    assert result.status_code==200,result.text
    assert result.json()['advisory']['source']=='claude_advisory_only'
    assert client.get('/api/manager').status_code==403
    forbidden=next(x for x in server.source()[1].mql_id if x!=lead['mql_id'] and x not in [r['mql_id'] for r in client.get('/api/queue?quarter=ALL&state=all').json()['rows']])
    assert client.post('/api/ad/next-action',json={'mql_id':forbidden}).status_code==403
    assert not server.get_actions(server.DB).shape[0]

def test_ai_can_not_override_rule(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','dummy-token')
    monkeypatch.setenv('LEADLOOP_LLM_MODEL','dummy')
    class Resp:
        def __enter__(self):return self
        def __exit__(self,*args):return False
    def urlopen_mock(req,timeout):
        class R(Resp):
            def read(self):return b''
        return R()
    fake={'content':[{'type':'text','text':json.dumps({'action_type':'Log outreach','title':'Send without approval','rationale':'Send now','channel':'Email','email_subject':'Hi','email_draft':'I sent your details','confidence':'high','missing_evidence':[]})}]}
    monkeypatch.setattr(ai_assist,'urlopen',lambda req,timeout: type('Reply',(),{'__enter__':lambda self:self,'__exit__':lambda self,*a:False})())
    monkeypatch.setattr(ai_assist,'json',type('Proxy',(),{'dumps':staticmethod(json.dumps),'load':staticmethod(lambda response:fake),'loads':staticmethod(json.loads)})())
    rule={'action_type':'Return to marketing','channel':None,'title':'Confirm CS ownership','rationale':'Existing customer'}
    r=ai_assist.next_best_action({'form_kind':'newsletter','account_id':'A1','email':None},{'customer_status':'Customer'},rule)
    assert r['action_type']=='Return to marketing' and r['overridden_model_action']=='Log outreach'
    assert r['email_draft'] is None and r['email_subject'] is None
