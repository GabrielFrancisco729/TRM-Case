"""Evidence calculations and manager-owned disposition categories."""
import sqlite3
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import server, core, advanced

ROOT=Path(__file__).resolve().parents[1]
DATA=core.load_data(ROOT/'data')
M=advanced.evidence_score(core.enriched(DATA),DATA)

def test_channel_evidence_is_observed_not_inferred():
    actual=advanced.acquisition_source_evidence(DATA,M)
    assert (actual['q2_form_mql'],actual['q3_form_mql'],actual['form_mql_decline_pct'])==(563,467,17.1)
    assert actual['organic_search_forms']=={'Q2':123,'Q3':113}
    assert actual['direct_plus_organic_forms']=={'Q2':219,'Q3':213}
    assert actual['paid_social_monthly_submissions']=={'Jul':33,'Aug':2,'Sep':18}
    assert (actual['paid_social_q2'],actual['paid_social_q3'])==(50,53)
    assert (actual['total_mql_drop'], actual['form_volume_drop'],actual['other_bridge_effects']) == (173,16,157)
    assert (actual['form_volume_share_pct'], actual['other_bridge_share_pct']) == (9.2,90.8)
    assert '16 / 173' in actual['interpretation'] or '16 raw form submissions' in actual['interpretation']
    assert 'not the definition of the 9%' in actual['interpretation']

def test_reasons_can_be_revised_undone_and_audited(tmp_path):
    db=tmp_path/'demo.sqlite';core.init_db(db)
    with sqlite3.connect(db) as conn:
        assert len(advanced.get_rejection_reasons(conn))==8
        with pytest.raises(ValueError):advanced.set_rejection_reasons(conn,['same','same'],'Unacceptable duplicates')
        with pytest.raises(ValueError):advanced.set_rejection_reasons(conn,['Short','Valid example'],'')
        advanced.set_rejection_reasons(conn,['No need','Wrong segment'],'Approved process update')
        assert advanced.get_rejection_reasons(conn)==['No need','Wrong segment']
        with pytest.raises(ValueError):core.add_action(db,'M1','AD','Change status','Checked',{'M1'},new_status='Disqualified',reason_code='Duplicate / already engaged',valid_reason_codes=advanced.get_rejection_reasons(conn))
        core.add_action(db,'M1','AD','Change status','Wrong segment',{'M1'},new_status='Disqualified',reason_code='Wrong segment',valid_reason_codes=advanced.get_rejection_reasons(conn))
        advanced.undo_rejection_reasons(conn)
        assert advanced.get_rejection_reasons(conn)==advanced.DEFAULT_REJECTION_REASONS
        assert conn.execute('SELECT COUNT(*) FROM reason_config').fetchone()[0]==2
        assert core.get_actions(db).iloc[0].reason_code=='Wrong segment'

def test_reason_api_is_manager_only_and_applied_to_ad(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'demo.sqlite')
    with TestClient(server.app) as client:
        assert client.post('/api/manager/reason-codes',json={'codes':['Not now','Not ICP'],'justification':'Review'}).status_code==401
        assert client.get('/api/reason-codes').status_code==401
        client.post('/api/login',json={'role':'manager','pin':'quillon-demo'})
        payload={'codes':['Not ready','Not suitable'],'justification':'Revised by Marketing Ops for clarity'}
        ok=client.post('/api/manager/reason-codes',json=payload)
        assert ok.status_code==200,ok.text
        assert client.get('/api/manager/rules').json()['reason_codes']==payload['codes']
        assert client.post('/api/manager/reason-codes',json={'codes':['same','same'],'justification':'bad categories'}).status_code==422
        client.post('/api/logout')
        ad=client.get('/api/users').json()['ads'][0]['user_id']
        client.post('/api/login',json={'role':'ad','user_id':ad})
        assert client.get('/api/reason-codes').json()['codes']==payload['codes']
        lead=client.get('/api/queue?quarter=Q3&state=needs_action').json()['rows'][0]['mql_id']
        wrong=client.post('/api/actions',json={'mql_id':lead,'action_type':'Change status','new_status':'Disqualified','reason_code':'No fit / wrong segment','note':'Rejected'})
        assert wrong.status_code==422
        right=client.post('/api/actions',json={'mql_id':lead,'action_type':'Change status','new_status':'Disqualified','reason_code':'Not suitable','note':'We confirmed this mismatch'})
        assert right.status_code==200,right.text
        assert client.post('/api/manager/reason-codes/undo').status_code==403
        client.post('/api/logout')
        client.post('/api/login',json={'role':'manager','pin':'quillon-demo'})
        assert client.post('/api/manager/reason-codes/undo').status_code==200
        assert client.get('/api/manager/rules').json()['reason_codes']==advanced.DEFAULT_REJECTION_REASONS

def test_diagnostic_endpoint_includes_campaign_caveat(tmp_path,monkeypatch):
    monkeypatch.setattr(server,'DB',tmp_path/'demo.sqlite')
    with TestClient(server.app) as client:
        result=client.get('/api/diagnosis')
        assert result.status_code==200
        evidence=result.json()['acquisition']
        assert evidence['paid_social_monthly_submissions']['Aug']==2
        assert evidence['form_mql_decline_pct']==17.1
