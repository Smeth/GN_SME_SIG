from dataclasses import replace
from datetime import date
import pytest
from test_restore_mysql import isolated_mysql
from test_month_backup_mysql import setup, snapshot
from sme_gend.services.month_backup import create_month_backup, restore_month_backup, inspect_month_backup


@pytest.fixture
def timeline(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM agent_passation_grade')
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL',grade_id=NULL,date_grade='2024-01-01',ndecision='BASELINE'")
    from sme_gend.services.agent_dossier_service import AgentDossierService
    from sme_gend.services.personnel_service import PersonnelService
    personnel = PersonnelService(settings)
    dossier = AgentDossierService(settings)
    return settings, connect, params, personnel, dossier


def grade_rows(connect, params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois,c.grade_libelle,DATE(c.date_grade),c.ndecision FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id ORDER BY p.annee_mois')
            return cur.fetchall()


def add_2025(timeline):
    settings, connect, params, personnel, dossier = timeline
    dossier.add_passation(personnel.get_by_id('1'), grade_libelle='ADC',
        date_passation=date(2025, 6, 15), ndecision='NOTE/2025', obs='Saisie dossier')


def test_past_passation_without_2025_archive_updates_2026_without_inventing_period(timeline):
    _, connect, params, _, _ = timeline
    add_2025(timeline)
    assert all(row[1:] == ('ADC', date(2025, 6, 15), 'NOTE/2025') for row in grade_rows(connect, params))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sme_periode WHERE annee_mois LIKE '2025%%'")
            assert cur.fetchone()[0] == 0


def test_replacing_2026_from_older_backup_preserves_and_reapplies_2025(timeline, tmp_path):
    settings, connect, params, _, _ = timeline
    archive = tmp_path / 'before_manual_grade.zip'
    create_month_backup(settings, '2026-09', archive)
    add_2025(timeline)
    before_other = snapshot(connect, params)
    for _ in range(2):
        result = restore_month_backup(settings, archive, confirmed_month='2026-09')
        assert result['grade_history_reapplied'] == 1
        assert all(row[1:] == ('ADC', date(2025, 6, 15), 'NOTE/2025') for row in grade_rows(connect, params))
        assert snapshot(connect, params) == before_other
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE date_passation='2025-06-15'")
            assert cur.fetchone()[0] == 1


def test_later_dated_grade_keeps_priority_after_restoring_2026(timeline, tmp_path):
    settings, connect, params, personnel, dossier = timeline
    archive = tmp_path / 'baseline.zip'
    create_month_backup(settings, '2026-09', archive)
    dossier.add_passation(personnel.get_by_id('1'), grade_libelle='COL',
        date_passation=date(2026, 9, 1), ndecision='NOTE/2026', obs='Saisie dossier')
    add_2025(timeline)
    assert [row[1] for row in grade_rows(connect, params)] == ['ADC', 'COL']
    restore_month_backup(settings, archive, confirmed_month='2026-09')
    assert [row[1] for row in grade_rows(connect, params)] == ['ADC', 'COL']
    assert grade_rows(connect, params)[-1][2:] == (date(2026, 9, 1), 'NOTE/2026')


def test_month_backup_contains_past_passation_without_its_year_archive(timeline, tmp_path):
    settings, connect, params, _, _ = timeline
    add_2025(timeline)
    archive = tmp_path / 'with_history.zip'
    create_month_backup(settings, '2026-09', archive)
    payload = inspect_month_backup(archive)
    assert len(payload['shared_passations']) == 1
    assert payload['tables']['agent_passation_grade'][0]['grade_libelle'] == 'ADC'
    # Simulate recovery of the archived history onto an existing baseline.
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('DELETE FROM agent_passation_grade')
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL',date_grade='2024-01-01',ndecision='BASELINE'")
    restore_month_backup(settings, archive, confirmed_month='2026-09')
    assert grade_rows(connect, params)[-1][1:] == ('ADC', date(2025, 6, 15), 'NOTE/2025')


def test_contradictory_archive_passation_rolls_back_without_losing_manual_history(timeline, tmp_path):
    settings, connect, params, personnel, dossier = timeline
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET grade_libelle='COL',date_grade='2025-06-15',ndecision='ARCHIVE NOTE' WHERE id=1")
    archive = tmp_path / 'conflicting.zip'
    create_month_backup(settings, '2026-09', archive)
    add_2025(timeline)
    before = grade_rows(connect, params)
    events = dossier.list_passations(personnel.get_by_id('1'))
    with pytest.raises(ValueError, match='Conflit de passation'):
        restore_month_backup(settings, archive, confirmed_month='2026-09')
    assert grade_rows(connect, params) == before
    assert dossier.list_passations(personnel.get_by_id('1')) == events


@pytest.mark.parametrize('grade,stamp,expected', [
    ('MDL', date(2024, 1, 1), 'ADC'),
    ('COL', date(2026, 10, 1), 'COL'),
])
def test_later_2026_archive_import_uses_effective_dates(timeline, grade, stamp, expected):
    settings, connect, params, _, _ = timeline
    add_2025(timeline)
    from sme_gend.services.agent_dossier_import_enrichment import enrich_dossier_after_import
    from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, MySQLConnection
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(3,'2026-10','2026-10-31','Fermé')")
                cur.execute('INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle,date_grade,ndecision) VALUES(1,3,%s,%s,%s)',
                            (grade, stamp, 'BASELINE' if grade == 'MDL' else 'OCT NOTE'))
        enrich_dossier_after_import(settings, '2026-10', ['42'])
        project_carriere_from_dossier_batch(settings, mois='2026-10', only_est_actuelle=False)
    assert grade_rows(connect, params)[-1][1] == expected
    assert grade_rows(connect, params)[0][1] == 'ADC'


def test_import_same_date_conflict_rolls_back_new_month(timeline):
    settings, connect, params, _, _ = timeline
    add_2025(timeline)
    from sme_gend.services.agent_dossier_import_enrichment import enrich_dossier_after_import
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, MySQLConnection
    before = grade_rows(connect, params)
    with pytest.raises(ValueError, match='Conflit de passation'):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(3,'2026-10','2026-10-31','Fermé')")
                    cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle,date_grade) VALUES(1,3,'COL','2025-06-15')")
            enrich_dossier_after_import(settings, '2026-10', ['42'])
    assert grade_rows(connect, params) == before
