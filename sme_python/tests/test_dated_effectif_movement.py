from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.services import dated_effectif_movement as movement


@pytest.fixture
def settings():
    return Settings(database_path=Path('unused.mdb'), mysql_view_mois='2026-11')


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda permission: True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: True)


def record(settings):
    return movement.DatedEffectifMovement(1, 45, 'PERTE', 'RETRAITE', date(2026, 11, 1),
        datetime(2026, 11, 2), 'synthetic', None, None, 1, 'legacy45', 'PERSONNE SYNTHÉTIQUE', mysql_database_key(settings))


@pytest.mark.parametrize('action', ['list', 'create', 'update', 'delete', 'resolve'])
def test_permission_denial_precedes_any_database_access(monkeypatch, action):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda _: False)
    connection = Mock(side_effect=AssertionError('No SQL on denied action'))
    monkeypatch.setattr(movement, 'MySQLConnection', connection)
    with pytest.raises(PermissionError):
        if action == 'list': movement.list_movements(None)
        if action == 'create': movement.create_movement(None, 1, 'GAIN', 'AJOUT', '2026-11-01')
        if action == 'update': movement.update_movement(None, None, category='GAIN', movement_kind='AJOUT', effective_date='2026-11-01', reason='correction')
        if action == 'delete': movement.delete_movement(None, None, reason='correction')
        if action == 'resolve': movement.resolve_selected_agent(None, '1')
    connection.assert_not_called()


@pytest.mark.parametrize('category,kind,stamp', [('GAIN','', '2026-11-01'), ('OTHER','AJOUT','2026-11-01'),
    ('PERTE','RETRAITE',None), ('GAIN','AJOUT',date.today()+timedelta(days=1))])
def test_nature_and_real_nonfuture_date_are_required_before_capability_sql(settings, allowed, monkeypatch, category, kind, stamp):
    capability = Mock(side_effect=AssertionError('No SQL for invalid fields'))
    monkeypatch.setattr(movement, '_capability', capability)
    with pytest.raises(ValueError):
        movement.create_movement(settings, 45, category, kind, stamp)
    capability.assert_not_called()


def test_reference_and_details_are_optional():
    assert movement._values('GAIN', ' AJOUT ', '2026-01-10', ' ', None) == ('GAIN','AJOUT',date(2026,1,10),None,None)


@pytest.mark.parametrize('admin,grant', [(False,True),(True,False)])
def test_correction_requires_both_admin_role_and_dedicated_permission(settings, monkeypatch, admin, grant):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda p: grant if p=='workflow.correct_movement' else True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: admin)
    with pytest.raises(PermissionError):
        movement.delete_movement(settings, record(settings), reason='correction')


@pytest.mark.parametrize('action', ['update','delete'])
def test_correction_and_deletion_require_reason_before_sql(settings, allowed, monkeypatch, action):
    monkeypatch.setattr(movement, '_capability', Mock(side_effect=AssertionError('No SQL')))
    with pytest.raises(ValueError, match='obligatoire'):
        if action == 'delete': movement.delete_movement(settings, record(settings), reason=' ')
        else: movement.update_movement(settings, record(settings), category='PERTE', movement_kind='RETRAITE', effective_date='2026-11-01', reason='')


def test_snapshot_cannot_be_applied_to_another_database(settings, allowed):
    other = replace(settings, mysql_database='other_synthetic')
    with pytest.raises(movement.MovementConflict):
        movement.delete_movement(other, record(settings), reason='correction')


def test_historical_date_without_archive_requires_privilege_and_reason(settings, allowed, monkeypatch):
    monkeypatch.setattr('sme_gend.services.active_month_guard.load_active_month_snapshot', lambda _: SimpleNamespace(active_month='2026-11'))
    with pytest.raises(ValueError, match='historique'):
        movement._declared_date_guard(settings, date(2025,2,5), '')
    # There is no period lookup/creation for 2025; the real historical date is retained.
    movement._declared_date_guard(settings, date(2025,2,5), 'Pièce historique vérifiée')
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: False)
    with pytest.raises(PermissionError):
        movement._declared_date_guard(settings, date(2025,2,5), 'motif')
    movement._declared_date_guard(settings, date(2026,11,1), '')


def test_same_day_opposite_or_duplicate_movement_is_explicitly_refused():
    cursor = Mock()
    cursor.fetchall.return_value = [(12, 'GAIN')]
    with pytest.raises(movement.MovementConflict, match='ambigus'):
        movement._day_available(cursor, 45, date(2026,11,1))
    movement._day_available(cursor, 45, date(2026,11,1), excluding=12)


def test_picker_alias_resolution_uses_parameterized_canonical_lookup(settings, allowed, monkeypatch):
    cursor = Mock()
    cursor.fetchone.side_effect = [None, (1,), (45,)]
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    connection = Mock()
    connection.cursor.return_value = cursor
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(movement, 'MySQLConnection', lambda _: SimpleNamespace(connect=lambda **k: connection))
    monkeypatch.setattr(movement, '_capability', lambda _: None)
    key = "old-identity' OR 1=1 --"
    assert movement.resolve_selected_agent(settings, key) == 45
    calls = cursor.execute.call_args_list
    assert calls[0].args[1] == (key,) and calls[2].args[1] == (key,)
    assert key not in calls[0].args[0] and key not in calls[2].args[0]


def test_admin_correction_permission_is_not_granted_to_operator_or_reader():
    from sme_gend.config.auth_permissions import admin_role_permissions, operateur_role_permissions, lecteur_role_permissions
    assert 'workflow.correct_movement' in admin_role_permissions()
    assert 'workflow.correct_movement' not in operateur_role_permissions()
    assert 'workflow.correct_movement' not in lecteur_role_permissions()
