"""New reviewer-facing regression tests for evidence labels, disposition, local handoff."""
from pathlib import Path
import sys, json, sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import core, advanced, server, ai_assist
import pytest

D=core.load_data(Path(__file__).resolve().parents[1]/'data')
M=advanced.evidence_score(core.enriched(D),D)

def test_new_data_quality_checks_and_denominators():
    checks={row['id']:row['count'] for row in advanced.risk_checks(D,M)}
    assert checks['auto-convert']==14
    assert checks['repeat-mql']==9
    b=advanced.operating_breakdown(D,M,'Q3')
    assert b['delivery_ownership_risk_buckets']==159
    assert (b['comparable_proxy_numerator'],b['comparable_proxy_denominator'])==(157,262)

def test_strict_reasons_and_legacy_sqlite_migration(tmp_path):
    db=tmp_path/'old.sqlite3'
    with sqlite3.connect(db) as conn:
        conn.execute('''CREATE TABLE actions(id INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            mql_id TEXT NOT NULL,actor TEXT NOT NULL,action_type TEXT NOT NULL,new_status TEXT,
            assigned_to TEXT,channel TEXT,note TEXT NOT NULL)''')
    core.init_db(db)
    assert 'reason_code' in [c[1] for c in sqlite3.connect(db).execute('PRAGMA table_info(actions)')]
    with pytest.raises(ValueError):core.add_action(db,'M1','AD','Change status','Bad fit',{'M1'},new_status='Disqualified')
    with pytest.raises(ValueError):core.add_action(db,'M1','AD','Return to marketing','Check qualification',{'M1'},reason_code='NOT_A_REASON')
    i=core.add_action(db,'M1','AD','Change status','Bad fit',{'M1'},new_status='Disqualified',reason_code='No fit / wrong segment')
    assert i>0
    assert core.get_actions(db).iloc[0].reason_code=='No fit / wrong segment'

def test_two_way_marketing_handoff_and_permissions(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'local.sqlite3')
    with TestClient(server.app) as client:
        ad=client.get('/api/users').json()['ads'][0]['user_id']
        client.post('/api/login',json={'role':'ad','user_id':ad})
        leads=client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows']
        assert leads
        lead=leads[0]['mql_id']
        assert client.post('/api/actions',json={'mql_id':lead,'action_type':'Return to marketing','note':'Review fit'}).status_code==422
        r=client.post('/api/actions',json={'mql_id':lead,'action_type':'Return to marketing',
                                           'reason_code':'Bad data / enrichment','note':'Need more account information'})
        assert r.status_code==200,r.text
        assert lead not in [x['mql_id'] for x in client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows']]
        assert client.get('/api/leads/'+lead).json()['lead']['effective_status']=='Marketing Review'
        assert client.post('/api/actions',json={'mql_id':lead,'action_type':'Log outreach','note':'x','channel':'Call'}).status_code==409
        assert client.post('/api/manager/return-to-ad',json={'mql_id':lead,'note':'Manager approval'}).status_code==403
        client.post('/api/logout')
        assert client.post('/api/login',json={'role':'manager','pin':'quillon-demo'}).status_code==200
        report=client.get('/api/manager?quarter=Q3').json()
        assert report['counts']['marketing_review']==1
        assert any(l['mql_id']==lead for l in report['returned_to_marketing'])
        released=client.post('/api/manager/return-to-ad',json={'mql_id':lead,'note':'Checked and approved for AD review'})
        assert released.status_code==200,released.text
        assert client.get('/api/manager?quarter=Q3').json()['counts']['marketing_review']==0
        client.post('/api/logout')
        client.post('/api/login',json={'role':'ad','user_id':ad})
        assert lead in [x['mql_id'] for x in client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows']]

def test_claude_uses_current_api_and_no_removed_sampling_arguments(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY','unit-only-not-real')
    monkeypatch.setenv('LEADLOOP_LLM_MODEL','unit-only-model')
    seen={}
    class Resp:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps({'content':[{'type':'text','text':json.dumps({'decision':'Send to AD','confidence':'low','rationale':'ok','observed_evidence':[],'missing_evidence':[]})}]}).encode()
    # urllib json.load uses .read()
    def fake_open(request,timeout):
        seen['headers']=request.headers
        seen['body']=json.loads(request.data)
        return Resp()
    monkeypatch.setattr(ai_assist,'urlopen',fake_open)
    result=ai_assist.recommend({'form_kind':'demo','fit_tier':'A'})
    assert result['decision']=='Send to AD'
    assert seen['headers']['Anthropic-version']=='2026-10-21'
    assert 'temperature' not in seen['body']
