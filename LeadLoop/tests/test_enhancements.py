import sys
from pathlib import Path
from sqlite3 import connect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import pytest
import core,advanced,server,ai_assist

D=core.load_data(Path(__file__).resolve().parents[1]/'data')
M=advanced.evidence_score(core.enriched(D),D)

def test_q3_complete_evidence_split():
    result=advanced.operating_breakdown(D,M)
    values={r['bucket']:r['mqls'] for r in result['rows']}
    assert sum(values.values()) == 467
    assert result['reported_worked']==117
    assert values['Seller activity in SLA, but status not updated']==40
    assert values['Existing customer, AD/CS handoff to review']==38
    assert values['Unassigned at creation / system queue']==70
    assert values['No verified delivered notification']==61

def test_score_explained_not_probability():
    r=M[M.form_kind.eq('demo')].iloc[0]
    assert r.queue_score>=55
    assert any('High-intent' in i['signal'] for i in r.score_explanation)
    w=advanced.DEFAULT_WEIGHTS.copy();w['demo']=60
    newer=advanced.evidence_score(core.enriched(D),D,w)
    new=newer[newer.mql_id.eq(r.mql_id)].iloc[0]
    assert new.queue_score>=r.queue_score

def test_config_validation_and_undo(tmp_path):
    with connect(tmp_path/'test.db') as db:
        a=advanced.config(db)
        w=a.copy(); w['fit_a']=32
        advanced.set_config(db,w,'Marketing ops override')
        assert advanced.config(db)['fit_a']==32
        with pytest.raises(ValueError):advanced.set_config(db,{**w,'fit_a':500},'bad')
        advanced.undo_config(db)
        assert advanced.config(db)==a

def test_review_append_only(tmp_path):
    with connect(tmp_path/'test.db') as db:
        with pytest.raises(ValueError):advanced.record_review(db,'M00001','manager','Send to CS','',{'decision':'Send to AD'})
        advanced.record_review(db,'M00001','manager','Send to CS','Verified existing customer',{'decision':'Send to CS'})
        advanced.record_review(db,'M00001','manager','Send to AD','New evidence',{'decision':'Send to AD'})
        n=db.execute('SELECT count(*) FROM qualification_reviews').fetchone()[0]
        assert n==2

def test_control_is_reproducible():
    ids=[str(x) for x in M.mql_id]
    assert [advanced.control_group(x) for x in ids]==[advanced.control_group(x) for x in ids]
    assert 0.15<=sum(advanced.control_group(x) for x in ids)/len(ids)<=0.25

def test_ai_requires_explicit_setup(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY',raising=False)
    monkeypatch.delenv('LEADLOOP_LLM_MODEL',raising=False)
    assert not ai_assist.configured()
    with pytest.raises(RuntimeError):ai_assist.recommend({})

def test_authorized_manager_rules_and_qualification(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'DB',tmp_path/'s.sqlite3')
    with TestClient(server.app) as client:
        assert client.get('/api/manager/rules').status_code==401
        assert client.post('/api/login',json={'role':'manager','pin':'quillon-demo'}).status_code==200
        assert client.get('/api/manager/rules').status_code==200
        assert client.get('/api/manager/qualification').status_code==200
        assert client.get('/api/manager/data-guard').status_code==200
        assert client.get('/api/diagnosis').json()['operational']['total']==467
        reject=client.post('/api/manager/qualification/review',json={'mql_id':str(M[M.form_kind.eq('demo')].iloc[0].mql_id),
            'decision':'Return to nurture','reason':'bad'});assert reject.status_code==422
        w=client.get('/api/manager/rules').json()['weights'];w['seniority_vp']=11
        assert client.post('/api/manager/rules',json={'weights':w,'reason':'Review seniority weights'}).status_code==200
        assert client.post('/api/manager/rules/undo').status_code==200

def test_approved_cs_handoff_updates_ad_queue(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'DB',tmp_path/'s2.sqlite3')
    with TestClient(server.app) as c:
        # Pick a non-hand-raiser customer currently owned by an active AD.
        d,m=server.source();q=m[(m.quarter=='Q3') & m.customer_status.eq('Customer') &
                                  ~m.form_kind.isin(['demo','contact','partner']) & m.current_owner.isin([u['user_id'] for u in server.known_ads(d)])]
        assert not q.empty
        candidate=q.iloc[0];leadid=str(candidate.mql_id)
        assert c.post('/api/login',json={'role':'manager','pin':'quillon-demo'}).status_code==200
        assert c.post('/api/manager/qualification/review',json={'mql_id':leadid,'decision':'Send to CS',
                                                                'reason':'Verified customer; coordinate expansion with CS'}).status_code==200
        assert c.get('/api/manager/qualification/export').status_code==200
        c.post('/api/logout')
        assert c.post('/api/login',json={'role':'ad','user_id':str(candidate.current_owner)}).status_code==200
        allrows=c.get('/api/queue?quarter=Q3&state=all').json()['rows']
        selected=next(x for x in allrows if x['mql_id']==leadid)
        assert selected['qualification_approved']=='Send to CS'
        assert selected['needs_ad_action'] is False
        assert c.post('/api/actions',json={'mql_id':leadid,'action_type':'Log outreach','channel':'Call','note':'test'}).status_code==409

def test_control_group_cannot_be_treated(monkeypatch,tmp_path):
    monkeypatch.setattr(server,'DB',tmp_path/'ctrl.sqlite3')
    case=M[(M.quarter=='Q3') & (~M.form_kind.isin(['demo','partner','contact']))]
    record=next(r for r in case.itertuples(index=False) if advanced.control_group(r.mql_id))
    with TestClient(server.app) as c:
        c.post('/api/login',json={'role':'manager','pin':'quillon-demo'})
        r=c.post('/api/manager/qualification/review',json={'mql_id':record.mql_id,
            'decision':'Send to AD','reason':'Should be blocked by control guard'})
        assert r.status_code==409

def test_workstreams_have_complete_exclusive_assignment():
    assert M.queue_lane.notna().all()
    assert len(M.queue_lane)==len(M)
    assert set(M.queue_lane)<= {'Hand-raisers','CS review','Qualified fit','Nurture / low signal','Aged / recycle review'}
