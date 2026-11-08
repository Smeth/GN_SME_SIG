from datetime import date
from types import SimpleNamespace
import pytest
from sme_gend.services.period_activity_report import fold_interval_facts,capture_period_activity


def fact(**changes):
    base=dict(source='agent_passation_grade',source_id=1,agent_id=7,date='2026-09-10',name='SYNTHETIC',kind='Grade',detail='ADC')
    base.update(changes);return base


def test_repeated_legacy_observations_are_not_counted_as_multiple_events():
    rows=fold_interval_facts([fact(),fact(source_id=2),fact(agent_id=8,source_id=3)])
    assert len(rows)==2 and rows[0]['observations']==2 and rows[0]['source_ids']==[1,2]
    assert not rows[0]['multiple_values']


def test_conflicting_observations_remain_visible_instead_of_arbitrary_choice():
    rows=fold_interval_facts([fact(),fact(source_id=2,detail='MDL')])
    assert len(rows)==2 and all(row['multiple_values'] for row in rows)


def test_interval_access_is_refused_before_sql(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    with pytest.raises(PermissionError):capture_period_activity(SimpleNamespace(),'2026-09-01','2026-09-30')


def test_reversed_interval_is_refused_before_sql(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:True)
    with pytest.raises(ValueError,match='précéder'):
        capture_period_activity(SimpleNamespace(),'2026-10-01','2026-09-30')
