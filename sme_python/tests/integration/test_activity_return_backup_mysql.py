"""Restored event IDs keep actual returns and other months' dependencies."""
from dataclasses import replace
from datetime import date

import pytest

from test_restore_mysql import isolated_mysql
from test_month_backup_mysql import setup, snapshot
from test_activity_completion_mysql import start_activity


@pytest.mark.parametrize('kind', ['mission', 'conge', 'desertion'])
def test_month_restore_remaps_return_and_preserves_closed_activity(isolated_mysql, monkeypatch, tmp_path, kind):
    from sme_gend.services.activity_completion import complete_activity, ACTIVITIES, recorded_return
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.month_backup import create_month_backup, restore_month_backup
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    personnel = PersonnelService(settings)
    repo, expected = start_activity(settings, kind)
    complete_activity(settings, personnel.get_by_id('1'), kind, '2026-09-17', expected=expected)
    table = ACTIVITIES[kind][0]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT id FROM {table} WHERE agent_id=1')
            old_id = cur.fetchone()[0]
    archive = tmp_path / 'returns.zip'
    create_month_backup(settings, '2026-09', archive)
    other = snapshot(connect, params)
    for _ in range(2):
        restore_month_backup(settings, archive, confirmed_month='2026-09')
        assert snapshot(connect, params) == other
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT id FROM {table} WHERE agent_id=1')
                new_id = cur.fetchone()[0]
                assert new_id != old_id
                assert recorded_return(cur, 1, table, new_id) == date(2026, 9, 17)
                cur.execute('SELECT id FROM carriere_situation WHERE sme_periode_id=1 AND agent_id=1')
                career = cur.fetchone()[0]
        assert personnel.get_by_id(str(career)).position_admin == 'Présent'
    if kind == 'desertion':
        repo = MySQLNumeriqueRepository(settings)
        fresh = repo.load_deserteurs()['42']
        repo.upsert_desertion('42', replace(fresh, reference_dossier='AFTER RESTORE'), expected=fresh)
        assert personnel.get_by_id(str(career)).position_admin == 'Présent'


@pytest.mark.parametrize('kind', ['position', 'stage_end'])
def test_existing_generated_reference_shapes_survive_month_restore(isolated_mysql, monkeypatch, tmp_path, kind):
    from sme_gend.services.month_backup import create_month_backup, restore_month_backup
    from sme_gend.services.automatic_position_links import automatic_source
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    if kind == 'position':
        from sme_gend.services.position_admin_events import add_position_event
        add_position_event(settings, '42', position_admin='Détaché', date_debut=date(2026, 9, 10),
            date_fin=date(2026, 9, 17), lieu='TEST', reference_decision='NOTE', observation=None, mois_sme='2026-09')
        prefix = 'sgi:auto:position:%'
    else:
        from sme_gend.services.personnel_service import _personnel_write
        from sme_gend.services.temporal_events import record_position
        with _personnel_write(settings):
            record_position(settings, 1, 'Présent', date(2026, 9, 17),
                reference='sgi:auto:stage_end:1:INTERNE:2026-09-17')
        prefix = 'sgi:auto:stage_end:%'
    archive = tmp_path / 'existing-formats.zip'
    create_month_backup(settings, '2026-09', archive)
    restore_month_backup(settings, archive, confirmed_month='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT reference_decision FROM agent_position_event WHERE reference_decision LIKE %s', (prefix,))
            references = cur.fetchall()
            assert references
            for (reference,) in references:
                table, event_id, _, _ = automatic_source(reference)
                cur.execute(f'SELECT COUNT(*) FROM {table} WHERE id=%s', (event_id,))
                assert cur.fetchone()[0] == 1


def test_month_purge_refuses_to_orphan_another_month_return(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.mysql_import_period import purge_sme_periode_for_reimport
    from sme_gend.services.month_backup import create_month_backup, restore_month_backup
    from sme_gend.services.activity_completion import complete_activity, recorded_return
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    from sme_gend.models.numerique_satellite import DeserteurDetail
    settings, connect, params = setup(isolated_mysql, monkeypatch, tmp_path)
    personnel = PersonnelService(settings)
    repo = MySQLNumeriqueRepository(settings)
    repo.upsert_desertion('42', DeserteurDetail('2026-08-20', reference_dossier='SOURCE AUGUST'))
    expected = repo.load_deserteurs()['42']
    complete_activity(settings, personnel.get_by_id('1'), 'desertion', '2026-09-17', expected=expected)
    august = tmp_path / 'august.zip'
    create_month_backup(settings, '2026-08', august)
    with pytest.raises(ValueError, match='autre mois dépend'):
        purge_sme_periode_for_reimport(settings, '2026-08', imported_only=False)
    assert personnel.get_by_id('1').position_admin == 'Présent'
    restore_month_backup(settings, august, confirmed_month='2026-08')
    assert personnel.get_by_id('1').position_admin == 'Présent'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM agent_desertion WHERE agent_id=1')
            event_id = cur.fetchone()[0]
            assert recorded_return(cur, 1, 'agent_desertion', event_id) == date(2026, 9, 17)
    # A subsequent reference correction makes the old archive incompatible
    # with the exact source now attested by September. The whole restore rolls back.
    fresh = repo.load_deserteurs()['42']
    repo.upsert_desertion('42', replace(fresh, reference_dossier='CORRECTED SOURCE'), expected=fresh)
    with pytest.raises(ValueError, match='ne restitue pas à l’identique'):
        restore_month_backup(settings, august, confirmed_month='2026-08')
    assert repo.load_deserteurs()['42'].reference_dossier == 'CORRECTED SOURCE'
    assert personnel.get_by_id('1').position_admin == 'Présent'
