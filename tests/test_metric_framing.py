"""Regression checks for the interview narrative and consistent metric definitions."""
from pathlib import Path
from fastapi.testclient import TestClient
import core,advanced,server

ROOT=Path(__file__).resolve().parents[1]

def test_9_91_split_is_accounting_for_reported_drop():
    data=core.load_data(ROOT/'data')
    m=advanced.evidence_score(core.enriched(data),data)
    evidence=advanced.acquisition_source_evidence(data,m)
    assert (evidence['total_mql_drop'], evidence['form_volume_drop'],evidence['other_bridge_effects'])==(173,16,157)
    assert evidence['form_volume_share_pct']==9.2
    assert evidence['other_bridge_share_pct']==90.8
    assert evidence['form_volume_drop']+evidence['other_bridge_effects']==evidence['total_mql_drop']
    assert (evidence['q2_form_mql'],evidence['q3_form_mql'],evidence['form_mql_decline_pct'])==(563,467,17.1)


def test_51_percent_is_primary_and_60_percent_is_secondary():
    data=core.load_data(ROOT/'data')
    m=advanced.evidence_score(core.enriched(data),data)
    b=advanced.operating_breakdown(data,m,'Q3')
    assert b['delivery_ownership_risk_buckets']==159
    assert (b['adjusted_adoption_numerator'],b['adjusted_adoption_denominator'])==(157,308)
    assert round(100*157/308,1)==51.0
    assert (b['comparable_proxy_numerator'],b['comparable_proxy_denominator'])==(157,262)
    assert round(100*157/262,1)==59.9
    assert b['adjusted_adoption_denominator']==b['total']-b['delivery_ownership_risk_buckets']


def test_api_and_walkthrough_use_same_definitions():
    with TestClient(server.app) as client:
        response=client.get('/api/diagnosis')
    assert response.status_code==200
    data=response.json()
    assert data['acquisition']['form_volume_share_pct']==9.2
    assert data['acquisition']['other_bridge_share_pct']==90.8
    assert data['operational']['adjusted_adoption_denominator']==308
    js=(ROOT/'static/app.js').read_text(encoding='utf-8')
    assert 'app.diagnosis.acquisition.form_volume_share_pct' in js
    assert 'd.adjusted_adoption_denominator' in js
    assert 'd.comparable_proxy_denominator' in js
    assert 'One shared definition' in js
    assert 'Owner revalidation on territory change' in js
    assert '9% causal demand loss' not in js


def test_reader_documentation_does_not_conflate_9_with_paid_social():
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    architecture=(ROOT/'docs/ARCHITECTURE.md').read_text(encoding='utf-8')
    assert '16 / 173 (9%)' in readme
    assert '157 / 308 = 51.0%' in readme
    assert '157 / 262 = 59.9%' in readme
    assert '9% always refers to 16 / 173' in readme
    assert 'One shared definition' in architecture or 'Unified worked definition' in architecture
    assert 'Owner revalidation after territory changes' in architecture
    for text in (readme,architecture):
        assert '9% incremental demand loss from paid social' not in text
        assert 'causal 9% lift/loss' not in text
