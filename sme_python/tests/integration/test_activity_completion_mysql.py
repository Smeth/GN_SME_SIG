"""Actual activity returns and concurrency against a disposable MySQL schema."""
from dataclasses import replace
from datetime import date

import pytest

from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def start_activity(settings, kind):
    from sme_gend.models.numerique_satellite import MissionDetail, DeserteurDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    repo = MySQLNumeriqueRepository(settings)
    if kind == 'mission':
        repo.upsert_mission('42', MissionDetail('CONTROLE', 'TEST', 'NOTE', '2026-09-10'))
    elif kind == 'desertion':
        repo.upsert_desertion('42', DeserteurDetail('2026-09-10', reference_dossier='DES/1'))
    else:
        AgentDossierEventsService(settings).add_conge('42', type_code='ADMIN',
            date_debut=date(2026, 9, 10), date_fin=None, quota_jours=None,
            observation='TEST', sme_mois='2026-09')
    return repo, activity_snapshot(repo, kind)


def activity_snapshot(repo, kind):
    if kind == 'mission':
        return repo.load_missions()['42']
    if kind == 'desertion':
        return repo.load_deserteurs()['42']
    return repo.load_latest_conges_for_period()['42']


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_midmonth_return_closes_event_projects_present_and_keeps_history(isolated_mysql, monkeypatch, kind):
    from sme_gend.services.activity_completion import complete_activity, activity_return_reference, ACTIVITIES
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    repo, expected = start_activity(settings, kind)
    row = personnel.get_by_id('1')
    result = complete_activity(settings, row, kind, '2026-09-17', expected=expected)
    assert result.changed and result.position_admin == 'Présent'
    assert personnel.get_by_id('1').position_admin == 'Présent'
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    audit = AppConfigRepository(settings)
    entries = audit.search_audit_journal(action='activity_completed', personnel_id='1',
        personnel_keys=audit.audit_personnel_keys('1'), broad_agent_search=True)
    assert len(entries) == 1
    table, _, finish = ACTIVITIES[kind]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM {table} WHERE agent_id=1')
            assert cur.fetchone()[0] == 1
            if finish:
                cur.execute(f'SELECT {finish} FROM {table} WHERE agent_id=1')
                assert cur.fetchone()[0] == date(2026, 9, 17)
            cur.execute('SELECT date_fin FROM agent_position_event WHERE agent_id=1 '
                        'AND reference_decision=%s AND position_admin<>%s',
                        (f'sgi:auto:{table}:{result.event_id}', 'Présent'))
            assert cur.fetchone()[0] == date(2026, 9, 17)
            cur.execute('SELECT date_debut FROM agent_position_event WHERE agent_id=1 AND reference_decision=%s',
                        (activity_return_reference(table, result.event_id),))
            assert cur.fetchone()[0] == date(2026, 9, 17)
            cur.execute("SELECT personnel_id,JSON_UNQUOTE(JSON_EXTRACT(payload,'$.personnel_nom')) "
                        "FROM audit_journal WHERE action='activity_completed'")
            assert cur.fetchone() == ('42', row.noms)
            cur.execute("INSERT INTO sme_periode(id,annee_mois,statut,date_dernier_jour_mois) "
                        "VALUES(2,'2026-10','Préparé','2026-10-31')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,position_admin_libelle) VALUES(2,1,2,'Mission')")
    from sme_gend.services.temporal_events import project_positions
    from sme_gend.services.personnel_service import _personnel_write
    with _personnel_write(settings):
        project_positions(settings, 1, date(2026, 9, 17))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT position_admin_libelle FROM carriere_situation WHERE id=2')
            assert cur.fetchone()[0] == 'Présent'
    fresh = activity_snapshot(repo, kind)
    result = complete_activity(settings, personnel.get_by_id('1'), kind, '2026-09-17', expected=fresh)
    assert not result.changed


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_concurrent_change_is_not_overwritten(isolated_mysql, monkeypatch, kind):
    from sme_gend.services.activity_completion import complete_activity, ACTIVITIES
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, kind)
    row = personnel.get_by_id('1')
    table = ACTIVITIES[kind][0]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'UPDATE {table} SET observation=%s WHERE agent_id=1', ('CONCURRENT',))
    with pytest.raises(ValueError, match='modifi'):
        complete_activity(settings, row, kind, '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin != 'Présent'


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_audit_failure_rolls_back_the_return(isolated_mysql, monkeypatch, kind):
    from sme_gend.services.activity_completion import complete_activity
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, kind)
    row = personnel.get_by_id('1')
    def fail(*a, **kw):
        raise RuntimeError('SYNTHETIC AUDIT FAILURE')
    monkeypatch.setattr('sme_gend.services.business_audit.record_business_action', fail)
    with pytest.raises(RuntimeError, match='AUDIT FAILURE'):
        complete_activity(settings, row, kind, '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin == row.position_admin
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_position_event WHERE reference_decision LIKE 'sgi:auto:activity_return:%'")
            assert cur.fetchone()[0] == 0


def test_closed_month_and_start_after_return_are_refused(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, 'mission')
    row = personnel.get_by_id('1')
    with pytest.raises(ValueError, match='précéder'):
        complete_activity(settings, row, 'mission', '2026-09-09', expected=expected)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=1")
    with pytest.raises(ValueError, match='clôturé'):
        complete_activity(settings, row, 'mission', '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin == 'Mission'


def test_desertion_reedit_preserves_return_and_delete_removes_its_marker(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    repo, expected = start_activity(settings, 'desertion')
    complete_activity(settings, personnel.get_by_id('1'), 'desertion', '2026-09-17', expected=expected)
    fresh = repo.load_deserteurs()['42']
    repo.upsert_desertion('42', replace(fresh, reference_dossier='DES/2'), expected=fresh)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    fresh = repo.load_deserteurs()['42']
    with pytest.raises(ValueError, match='retour daté'):
        repo.upsert_desertion('42', replace(fresh, dat_desertion='2026-09-20'), expected=fresh)
    assert repo.load_deserteurs()['42'].dat_desertion == '2026-09-10'
    repo.delete_desertion('42', expected=repo.load_deserteurs()['42'])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_position_event WHERE reference_decision LIKE 'sgi:auto:activity_return:%'")
            assert cur.fetchone()[0] == 0


@pytest.mark.parametrize('field,value', [('reference_dossier', 'NOTE-B'), ('session', 'SESSION-B')])
def test_stage_single_field_concurrent_change_invalidates_snapshot(isolated_mysql, monkeypatch, field, value):
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, _, _, _, _ = setup_services(isolated_mysql, monkeypatch)
    repo = MySQLNumeriqueRepository(settings)
    repo.upsert_stage('42', StageDetail('FORMATION', 'TEST', '2026-09-10',
        reference_dossier='NOTE-A', session='SESSION-A'), type_code='LOCAL', date_debut='2026-09-10')
    stale = repo.load_stages_loc()['42']
    repo.upsert_stage('42', replace(stale, **{field: value}), type_code='LOCAL',
                      date_debut='2026-09-10', expected=stale)
    assert repo.load_stages_loc()['42'].write_token != stale.write_token
    with pytest.raises(ValueError, match='modifiée'):
        repo.upsert_stage('42', replace(stale, nature_stage='STALE CHANGE'),
                          type_code='LOCAL', date_debut='2026-09-10', expected=stale)
    assert getattr(repo.load_stages_loc()['42'], field) == value


def test_desertion_reference_change_invalidates_snapshot(isolated_mysql, monkeypatch):
    settings, _, _, _, _ = setup_services(isolated_mysql, monkeypatch)
    repo, stale = start_activity(settings, 'desertion')
    repo.upsert_desertion('42', replace(stale, reference_dossier='DES/2'), expected=stale)
    assert repo.load_deserteurs()['42'].write_token != stale.write_token
    with pytest.raises(ValueError, match='modifiée'):
        repo.upsert_desertion('42', replace(stale, reference_dossier='STALE'), expected=stale)


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_activity_module_button_really_records_the_dated_return(isolated_mysql, monkeypatch, kind):
    from importlib import import_module
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtWidgets import QMessageBox
    from sme_gend.ui.activity_return_action import ActivityReturnDialog
    settings, _, _, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, kind)
    row = personnel.get_by_id('1')
    monkeypatch.setattr(ActivityReturnDialog, 'run', lambda *a, **kw: date(2026, 9, 17))
    module_name, cls, method = {
        'mission': ('missions_view', 'MissionsView', '_end_checked_missions'),
        'conge': ('conges_view', 'CongesView', '_reset_checked'),
        'desertion': ('desertion_view', 'DesertionView', '_reset_checked'),
    }[kind]
    module = import_module('sme_gend.ui.views.' + module_name)
    view = SimpleNamespace(_checked={'42'}, _check_can_edit=lambda: True,
        _scoped_settings=settings, _rows=[row], _details={'42': expected},
        _row_key=lambda row: row.satellite_join_key(), _view_data=SimpleNamespace(mark_stale=Mock()),
        _reload_lists_async=Mock(), _display_rows=[SimpleNamespace(liste=row, conge=expected)],
        _movement_key=lambda movement: movement.liste.satellite_join_key())
    getattr(getattr(module, cls), method)(view)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    assert not view._checked
    assert '1 retour(s) enregistré(s) au 17/09/2026' in QMessageBox.information.call_args.args[2]


def test_mission_reedit_cannot_reopen_a_completed_event(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    settings, _, _, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    repo, expected = start_activity(settings, 'mission')
    complete_activity(settings, personnel.get_by_id('1'), 'mission', '2026-09-17', expected=expected)
    fresh = repo.load_missions()['42']
    repo.upsert_mission('42', replace(fresh, refnds='NEW REF'), expected=fresh)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    fresh = repo.load_missions()['42']
    with pytest.raises(ValueError, match='retour daté'):
        repo.upsert_mission('42', replace(fresh, date_depart='2026-09-20'), expected=fresh)
    assert repo.load_missions()['42'].date_depart == '2026-09-10'


def test_return_does_not_replace_a_newer_other_activity(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.models.numerique_satellite import MaladeDetail
    settings, _, _, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    repo, expected = start_activity(settings, 'mission')
    original = personnel.get_by_id('1')
    repo.upsert_malade('42', MaladeDetail('2026-09-12', 'TEST', None))
    fresh = personnel.get_by_id('1')
    assert fresh.position_admin == 'Malade'
    with pytest.raises(ValueError, match='situation.*changé'):
        complete_activity(settings, original, 'mission', '2026-09-17', expected=expected)
    with pytest.raises(ValueError, match='autre position'):
        complete_activity(settings, fresh, 'mission', '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin == 'Malade'


def test_postcommit_mirror_error_keeps_the_confirmed_return(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services import business_audit
    from sme_gend.repositories.mysql_connection import after_mysql_commit, CommittedMirrorError
    settings, _, _, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, 'mission')
    original = business_audit.record_business_action
    def audit(*a, **kw):
        original(*a, **kw)
        def fail():
            raise OSError('SYNTHETIC MIRROR FAILURE')
        after_mysql_commit(fail)
    monkeypatch.setattr(business_audit, 'record_business_action', audit)
    with pytest.raises(CommittedMirrorError):
        complete_activity(settings, personnel.get_by_id('1'), 'mission', '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin == 'Présent'


def test_return_works_for_native_agent_without_legacy_identifier(isolated_mysql, monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, 'conge')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            # The deployed schema forbids NULL; an empty legacy key exercises
            # the same UI fallback without weakening that schema contract.
            cur.execute("UPDATE agent SET legacy_access_id='' WHERE id=1")
    row = personnel.get_by_id('1')
    assert row.access_join_id is None
    result = complete_activity(settings, row, 'conge', '2026-09-17', expected=expected)
    assert result.position_admin == 'Présent'
    assert personnel.get_by_id('1').position_admin == 'Présent'


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_newer_same_kind_activity_invalidates_old_return(isolated_mysql, monkeypatch, kind):
    from sme_gend.services.activity_completion import complete_activity, ACTIVITIES
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, kind)
    old_row = personnel.get_by_id('1')
    events = AgentDossierEventsService(settings)
    if kind == 'conge':
        events.add_conge('42', type_code='ADMIN', date_debut=date(2026, 9, 12), date_fin=None,
                         quota_jours=None, observation='NEW', sme_mois='2026-09')
    elif kind == 'mission':
        events.add_mission('42', date_debut=date(2026, 9, 12), date_fin=None, nature='NEW',
                           lieu=None, reference_dossier=None, observation=None)
    else:
        events.add_desertion('42', date_desertion=date(2026, 9, 12), observation='NEW')
    assert personnel.get_by_id('1') == old_row
    with pytest.raises(ValueError, match='autre activité'):
        complete_activity(settings, old_row, kind, '2026-09-17', expected=expected)
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    refreshed = activity_snapshot(MySQLNumeriqueRepository(settings), kind)
    with pytest.raises(ValueError, match='Plusieurs fiches'):
        complete_activity(settings, personnel.get_by_id('1'), kind, '2026-09-17', expected=refreshed)
    assert personnel.get_by_id('1').position_admin == old_row.position_admin
    table, _, finish = ACTIVITIES[kind]
    if finish:
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT {finish} FROM {table} ORDER BY id')
                assert cur.fetchall() == ((None,), (None,))


@pytest.mark.parametrize('kind,label', [('mission', 'Mission'), ('conge', 'CongéAdmin'), ('desertion', 'Déserteur')])
def test_direct_newer_same_label_position_is_not_masked(isolated_mysql, monkeypatch, kind, label):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services.temporal_events import record_position
    from sme_gend.services.personnel_service import _personnel_write
    settings, _, _, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    _, expected = start_activity(settings, kind)
    old_row = personnel.get_by_id('1')
    with _personnel_write(settings):
        record_position(settings, 1, label, date(2026, 9, 12), reference='SYNTHETIC NEW POSITION')
    assert personnel.get_by_id('1') == old_row
    with pytest.raises(ValueError, match='autre position ou activité'):
        complete_activity(settings, old_row, kind, '2026-09-17', expected=expected)
    assert personnel.get_by_id('1').position_admin == label
