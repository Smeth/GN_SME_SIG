"""Temporal sources, targeted projections and administrative return corrections."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from threading import Barrier, Event
from unittest.mock import Mock

import pytest

from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services
from test_publication_registry_mysql import install_registry
from test_activity_completion_mysql import start_activity


def migrated(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params, personnel, dossier = setup_services(isolated_mysql, monkeypatch)
    install_registry(settings, tmp_path, monkeypatch)
    return settings, connect, params, personnel, dossier


def test_parallel_writers_share_gate_but_publication_waits_for_commit(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.repositories.mysql_connection import MySQLConnection
    settings, _, _, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    rendezvous = Barrier(2)
    admitted = Event()
    release = Event()
    entered = Event()
    def write(aid):
        with _personnel_write(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('UPDATE agent SET noms=%s WHERE id=%s', ('CONCURRENT', aid))
            if rendezvous.wait(timeout=5) == 0:
                admitted.set()
            assert release.wait(timeout=10)
    def publish():
        with publication_write_barrier(settings):
            entered.set()
    with ThreadPoolExecutor(max_workers=3) as workers:
        first, second = workers.submit(write, 1), workers.submit(write, 2)
        assert admitted.wait(timeout=7)
        blocked = workers.submit(publish)
        assert not entered.wait(timeout=.1)
        release.set()
        first.result(timeout=15)
        second.result(timeout=15)
        blocked.result(timeout=15)
    assert entered.is_set()


@pytest.mark.parametrize('legacy_label', ['2026-09', '202609', '092026'])
def test_prepared_month_carries_observation_and_applies_only_later_dated_facts(isolated_mysql, monkeypatch, tmp_path, legacy_label):
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.models.numerique_satellite import MissionDetail
    settings, connect, params, _, dossier = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE sme_periode SET annee_mois=%s WHERE id=1', (legacy_label,))
            cur.execute("UPDATE carriere_situation SET grade_libelle='OBSERVED',obs='Raw Access observation' WHERE id=1")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'OLD','2025-01-01'),(1,'NEW','2026-10-03')")
    MySQLNumeriqueRepository(settings).upsert_mission('42', MissionDetail('FUTURE', None, None, '2026-10-05'))
    with publication_write_barrier(settings), period_state_write(settings, reason='test_projection'):
        result = prepare_month_projection(settings, '2026-10', '2026-09')
    assert result.created_rows == 1 and result.projected_agents == 1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle,position_admin_libelle,obs FROM carriere_situation WHERE sme_periode_id=%s', (result.period_id,))
            assert cur.fetchone() == ('NEW', 'Mission', 'Raw Access observation')
            cur.execute('SELECT COUNT(*) FROM agent_mission')
            assert cur.fetchone()[0] == 1
    with publication_write_barrier(settings), period_state_write(settings, reason='test_projection'):
        with pytest.raises(ValueError, match='contient déjà'):
            prepare_month_projection(settings, '2026-10', '2026-09')


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_correct_validated_return_keeps_source_authority_and_append_only_evidence(isolated_mysql, monkeypatch, tmp_path, kind):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services.activity_return_correction import load_activity_return, correct_activity_return
    settings, connect, params, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    _, expected = start_activity(settings, kind)
    result = complete_activity(settings, personnel.get_by_id('1'), kind, '2026-09-17', expected=expected)
    table = {'mission': 'agent_mission', 'conge': 'agent_conge', 'desertion': 'agent_desertion'}[kind]
    snapshot = load_activity_return(settings, 1, table, result.event_id)
    corrected = correct_activity_return(settings, snapshot, '2026-09-16', 'Date vérifiée sur la note', reference='NOTE/CORR')
    assert corrected.return_date == date(2026, 9, 16) and corrected.reference == 'NOTE/CORR'
    assert personnel.get_by_id('1').position_admin == 'Présent'
    with pytest.raises(ValueError, match='modifié depuis'):
        correct_activity_return(settings, snapshot, '2026-09-15', 'STALE')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT effective_from,recorded_at,reason,JSON_UNQUOTE(JSON_EXTRACT(before_state,'$.date_retour')),"
                        "JSON_UNQUOTE(JSON_EXTRACT(after_state,'$.date_retour')) FROM sme_temporal_change WHERE mutation='correct_return'")
            row = cur.fetchone()
            assert row[0] == date(2026, 9, 16) and row[1].date() > row[0]
            assert row[2:] == ('Date vérifiée sur la note', '2026-09-17', '2026-09-16')
            cur.execute("SELECT state FROM sme_projection_dirty WHERE agent_id=1 AND component='position'")
            assert cur.fetchone()[0] == 'done'


def test_return_correction_cannot_hide_another_activity_or_reorder_a_note_edit(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services.activity_return_correction import load_activity_return, correct_activity_return
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings, _, _, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    _, expected = start_activity(settings, 'mission')
    result = complete_activity(settings, personnel.get_by_id('1'), 'mission', '2026-09-17', expected=expected)
    AgentDossierEventsService(settings).add_mission('42', date_debut=date(2026,9,18), date_fin=None,
        nature='NEXT', lieu=None, reference_dossier=None, observation=None)
    snapshot = load_activity_return(settings, 1, 'agent_mission', result.event_id)
    with pytest.raises(ValueError, match='chevaucherait'):
        correct_activity_return(settings, snapshot, '2026-09-20', 'Delayed return')
    corrected = correct_activity_return(settings, snapshot, '2026-09-16', 'Earlier return')
    assert personnel.get_by_id('1').position_admin == 'Mission'
    correct_activity_return(settings, corrected, '2026-09-16', 'Reference only', reference='NEW NOTE')
    assert personnel.get_by_id('1').position_admin == 'Mission'


@pytest.mark.parametrize('code', ['LOCAL', 'INTERNE', 'EXTERIEUR'])
def test_legacy_stage_return_can_be_corrected_without_changing_diploma(isolated_mysql, monkeypatch, tmp_path, code):
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.temporal_events import record_position
    from sme_gend.services.activity_return_correction import list_activity_returns, correct_activity_return
    settings, connect, params, personnel, dossier = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO ref_type_stage(code,libelle) VALUES('INTERNE','Interne')")
    repo = MySQLNumeriqueRepository(settings)
    repo.upsert_stage('42', StageDetail('TEST', 'SCHOOL', '2026-09-10 — 2026-09-17'),
                      type_code=code, date_debut='2026-09-10', date_fin='2026-09-17')
    with _personnel_write(settings):
        record_position(settings, 1, 'Présent', date(2026,9,17), reference=f'sgi:auto:stage_end:1:{code}:2026-09-17')
    dossier.add_diplome(personnel.get_by_id('1'), categorie='MILITAIRE', libelle='STAGE DIPLOMA', date_obtention=date(2026,9,17))
    snapshot = list_activity_returns(settings, 1)[0]
    corrected = correct_activity_return(settings, snapshot, '2026-09-16', 'Actual end corrected')
    assert corrected.return_date == date(2026,9,16)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    diplomas = dossier.list_diplomes(personnel.get_by_id('1'))
    assert len(diplomas) == 1 and diplomas[0].date_obtention == date(2026,9,17)


def test_dirty_bounds_are_per_agent_component_and_reset_after_processing(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.temporal_changes import queue_projection, acknowledge_projection
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.month_projection import refresh_month_projection
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2020-01','2020-01-31','Fermé')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,diplome_libelle) VALUES(2,1,2,'OBS A','OBS A DIP'),(3,2,2,'OBS B','OBS B DIP'),(4,2,1,'B NOW','B NOW DIP')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'DATED A','2019-01-01')")
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(2,'MILITAIRE','DATED B','2019-01-01')")
    with _personnel_write(settings):
        queue_projection(settings, 1, 'grade', date(2019,1,1))
        token = queue_projection(settings, 2, 'diplome', date(2026,9,1))
    with publication_write_barrier(settings):
        refresh_month_projection(settings, '2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT diplome_libelle FROM carriere_situation WHERE id=3')
            assert cur.fetchone()[0] == 'OBS B DIP'
    with _personnel_write(settings):
        acknowledge_projection(settings, 2, 'diplome', token)
        queue_projection(settings, 2, 'diplome', date(2026,10,1))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT effective_from FROM sme_projection_dirty WHERE agent_id=2 AND component='diplome'")
            assert cur.fetchone()[0] == date(2026,10,1)


def test_journal_keeps_unknown_legacy_date_unknown_and_records_source_deletion(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params, personnel, dossier = migrated(isolated_mysql, monkeypatch, tmp_path)
    row = personnel.get_by_id('1')
    identifier = dossier.add_diplome(row, categorie='MILITAIRE', libelle='OBSERVATION', date_obtention=None,
                                   obs='Access 2025, date exacte non connue')
    snapshot = next(value for value in dossier.list_diplomes(row) if value.id == identifier)
    dossier.delete_diplome(row, identifier, expected=snapshot)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT mutation,effective_from FROM sme_temporal_change WHERE source_table='agent_diplome' ORDER BY id")
            assert cur.fetchall() == (('create', None), ('delete', None))


def test_correction_journal_failure_rolls_back_source_and_return(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services.activity_return_correction import load_activity_return, correct_activity_return
    settings, _, _, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    _, expected = start_activity(settings, 'mission')
    result = complete_activity(settings, personnel.get_by_id('1'), 'mission', '2026-09-17', expected=expected)
    original = load_activity_return(settings, 1, 'agent_mission', result.event_id)
    def fail(*a, **kw):
        raise RuntimeError('SYNTHETIC JOURNAL FAILURE')
    monkeypatch.setattr('sme_gend.services.temporal_changes.record_source_change', fail)
    with pytest.raises(RuntimeError, match='JOURNAL FAILURE'):
        correct_activity_return(settings, original, '2026-09-16', 'Synthetic correction')
    assert load_activity_return(settings, 1, 'agent_mission', result.event_id) == original


def test_actual_return_does_not_create_a_next_day_present_over_a_future_mission(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    settings, connect, params, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    _, expected = start_activity(settings, 'mission')
    AgentDossierEventsService(settings).add_mission('42', date_debut=date(2026,10,1), date_fin=None,
        nature='NEXT MONTH', lieu=None, reference_dossier=None, observation=None)
    complete_activity(settings, personnel.get_by_id('1'), 'mission', '2026-09-30', expected=expected)
    with publication_write_barrier(settings), period_state_write(settings, reason='test_projection'):
        result = prepare_month_projection(settings, '2026-10', '2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT position_admin_libelle FROM carriere_situation WHERE sme_periode_id=%s', (result.period_id,))
            assert cur.fetchone()[0] == 'Mission'


def test_earlier_stage_return_cannot_hide_a_mission_already_open_at_that_date(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.services.temporal_events import record_position
    from sme_gend.services.activity_return_correction import list_activity_returns, correct_activity_return
    settings, _, _, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    MySQLNumeriqueRepository(settings).upsert_stage('42', StageDetail('TEST', 'SCHOOL', '2026-09-01 — 2026-09-20'),
        type_code='LOCAL', date_debut='2026-09-01', date_fin='2026-09-20')
    AgentDossierEventsService(settings).add_mission('42', date_debut=date(2026,9,10), date_fin=None,
        nature='ANOTHER ACTIVITY', lieu=None, reference_dossier=None, observation=None)
    with _personnel_write(settings):
        record_position(settings, 1, 'Présent', date(2026,9,20), reference='sgi:auto:stage_end:1:LOCAL:2026-09-20')
    snapshot = list_activity_returns(settings, 1)[0]
    with pytest.raises(ValueError, match='encore ouverte'):
        correct_activity_return(settings, snapshot, '2026-09-15', 'Earlier stage end')
    assert list_activity_returns(settings, 1)[0] == snapshot


@pytest.mark.parametrize('kind,code', [('local','LOCAL'), ('interne','INTERNE'), ('ex','EXTERIEUR')])
def test_modern_stage_end_keeps_next_day_future_mission_and_records_actual_return(isolated_mysql, monkeypatch, tmp_path, kind, code):
    from types import SimpleNamespace
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    from sme_gend.services.activity_return_correction import list_activity_returns
    settings, connect, params, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO ref_type_stage(code,libelle) VALUES('INTERNE','Interne')")
    sat = MySQLNumeriqueRepository(settings)
    sat.upsert_stage('42', StageDetail('FORMATION', 'SCHOOL', '2026-09-10'), type_code=code, date_debut='2026-09-10')
    AgentDossierEventsService(settings).add_mission('42', date_debut=date(2026,10,1), date_fin=None,
        nature='NEXT MONTH', lieu=None, reference_dossier=None, observation=None)
    row = personnel.get_by_id('1')
    details = {'local': sat.load_stages_loc, 'interne': sat.load_stages_internal, 'ex': sat.load_stages_ex}[kind]()
    monkeypatch.setattr(StageEndDiplomaDialog, 'run', lambda *a, **kw: {'_stage_end_date':'2026-09-30'})
    view = SimpleNamespace(_scoped_settings=settings, _sat=sat, _checked={kind:{'42'}},
        _check_can_edit=lambda **kw:True, _keys_for_start_action=lambda _:['42'],
        _confirm_end_stages=lambda *a:True, _resolve_start_stage_rows=lambda *a:[row],
        _stage_end_reference_date=lambda:'2026-09-30', _prepare_stage_rows=lambda rows:{row.id:row},
        _details_for=lambda _:details, _invalidate_liste_caches=Mock(),
        _view_data=SimpleNamespace(mark_stale=Mock()), _reload_lists_async=Mock())
    module.StagesView._end_checked_stages(view, kind)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    assert list_activity_returns(settings, 1)[0].return_date == date(2026,9,30)
    with publication_write_barrier(settings), period_state_write(settings, reason='stage_next_month'):
        result = prepare_month_projection(settings, '2026-10', '2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT position_admin_libelle FROM carriere_situation WHERE sme_periode_id=%s', (result.period_id,))
            assert cur.fetchone()[0] == 'Mission'
