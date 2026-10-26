from pathlib import Path
import pandas as pd
import pytest
from core import load_data,enriched,volume_waterfall,not_worked_breakdown,init_db,add_action,get_actions,overlay_actions

DATA=Path(__file__).parent/'data'

def test_full_reconciliation():
    d=load_data(DATA);m=enriched(d);w=volume_waterfall(d,m)
    assert (w['q2_mql'],w['q3_mql'])==(640,467)
    assert (w['q2_forms'],w['q3_forms'])==(563,547)
    assert (w['q2_imports'],w['missing_q3'],w['missing_v2'])==(77,80,48)
    assert int(m[m.quarter=='Q2'].reported_worked.sum())==230
    assert int(m[m.quarter=='Q3'].reported_worked.sum())==117
    assert int(not_worked_breakdown(m,'Q3').mqls.sum())==467

def test_north_america_not_null():
    d=load_data(DATA);m=enriched(d)
    assert (d['accounts'].region=='NA').any()
    assert (m.region=='NA').sum()>0
    assert (d['team'].region=='NA').any()

def test_status_and_sla_must_not_count_late_system_conversion():
    d=load_data(DATA);m=enriched(d)
    assert int(m.system_late_conversion.sum())==14
    assert (m[m.system_late_conversion].reported_worked==False).all()
    assert int(m[(m.quarter=='Q3')&m.unassigned_at_creation].shape[0])==70
    assert int(m[(m.quarter=='Q3')&m.alert_failed].shape[0])==61

def test_audit_writeback(tmp_path):
    db=tmp_path/'audit.sqlite'
    d=load_data(DATA);m=enriched(d)
    who=set(m.mql_id);valid={'AD-NA-01','AD-NA-04'}
    add_action(db,'M00001','AD-NA-01','Log outreach','Called',who,channel='Call',valid_users=valid)
    add_action(db,'M00001','AD-NA-01','Change status','Good fit',who,new_status='Working',valid_users=valid)
    add_action(db,'M00001','AD-NA-01','Reassign','Territory moved',who,assigned_to='AD-NA-04',valid_users=valid)
    actions=get_actions(db)
    assert len(actions)==3
    changed=overlay_actions(m,actions).set_index('mql_id').loc['M00001']
    assert changed.effective_status=='Working'
    assert changed.effective_owner=='AD-NA-04'
    assert changed.demo_action_count==3

def test_disqualification_requires_reason(tmp_path):
    with pytest.raises(ValueError,match='reason'):
        add_action(tmp_path/'db.sqlite','M00001','AD-NA-01','Change status','',{'M00001'},new_status='Disqualified')
