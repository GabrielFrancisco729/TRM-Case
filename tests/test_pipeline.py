from fastapi.testclient import TestClient

import server


def client():
    return TestClient(server.app)


def test_stages_cover_the_whole_flow():
    stages = client().get('/api/pipeline/stages').json()['stages']
    assert [s['id'] for s in stages] == ['capture','identity','enrich','qualify','validate','route','notify','workspace','feedback']
    for s in stages:
        assert s['executors'] and s['logic'] and s['failure']


def test_run_recovers_every_hand_raiser_and_silent_failure():
    r = client().post('/api/pipeline/run', json={'ai': 'offline'}).json()
    assert sum(r['outcomes'].values()) == 547
    rows = {c['metric']: c for c in r['compare']}
    hand = next(c for k, c in rows.items() if k.startswith('Hand-raisers'))
    assert hand['leadloop'].startswith('108')
    assert all(l['owner'] for l in r['leads'] if l['outcome'] == 'AD inbox')
    assert r['value']['pipeline_at_risk'] > 0


def test_decisions_need_a_reason_and_the_right_owner():
    c = client()
    r = c.post('/api/pipeline/run', json={'ai': 'offline'}).json()
    lead = next(l for l in r['leads'] if l['outcome'] == 'AD inbox')
    bad = c.post('/api/pipeline/decision', json={'submission_id': lead['sid'], 'decision': 'reject', 'via': 'slack'})
    assert bad.status_code == 422
    ok = c.post('/api/pipeline/decision', json={'submission_id': lead['sid'], 'decision': 'accept', 'via': 'slack'})
    assert ok.status_code == 200 and ok.json()['actor'] == lead['owner']
    assert c.post('/api/pipeline/decision', json={'submission_id': lead['sid'], 'decision': 'accept'}).status_code == 401
    trace = c.get(f"/api/pipeline/trace/{lead['sid']}").json()
    assert trace['steps'][-1]['stage'] == 'feedback'
