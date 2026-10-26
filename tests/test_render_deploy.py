import base64
from fastapi.testclient import TestClient
import server


def test_healthz_unprotected(monkeypatch):
    monkeypatch.setenv('LEADLOOP_SITE_PASSWORD','super-secret-example')
    r=TestClient(server.app).get('/healthz')
    assert r.status_code==200 and r.json()['status']=='ok'

def test_reviewer_password_protects_site_and_api(monkeypatch):
    monkeypatch.setenv('LEADLOOP_SITE_PASSWORD','super-secret-example')
    monkeypatch.setenv('LEADLOOP_SITE_USER','reviewer')
    c=TestClient(server.app)
    assert c.get('/').status_code==401
    assert c.get('/api/diagnosis').status_code==401
    bad=base64.b64encode(b'reviewer:wrong').decode()
    assert c.get('/',headers={'Authorization':'Basic '+bad}).status_code==401
    good=base64.b64encode(b'reviewer:super-secret-example').decode()
    assert c.get('/',headers={'Authorization':'Basic '+good}).status_code==200
    assert c.get('/api/pipeline/stages',headers={'Authorization':'Basic '+good}).status_code==200

def test_default_local_access_unchanged(monkeypatch):
    monkeypatch.delenv('LEADLOOP_SITE_PASSWORD',raising=False)
    assert TestClient(server.app).get('/').status_code==200
