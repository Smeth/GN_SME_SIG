"""A dated loss changes visibility, preserving the physical monthly observation."""
from datetime import date

import pytest
from test_restore_mysql import isolated_mysql
from test_temporal_projection_migration_mysql import migrated


@pytest.mark.parametrize('month_label', ['2026-09', '202609', '092026'])
def test_loss_scope_agrees_in_list_advancement_organigram_and_access_export(
    isolated_mysql, monkeypatch, tmp_path, month_label,
):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository, _select_liste
    from sme_gend.repositories.mysql_avancement_repository import MySQLAvancementRepository
    from sme_gend.repositories.mysql_organigram_repository import MySQLOrganigramRepository
    from sme_gend.services.mysql_bundle_export_to_access import _fetch_liste_rows
    from sme_gend.services.membership_scope import membership_projection_enabled
    settings, connect, params, personnel, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE sme_periode SET annee_mois=%s WHERE id=1', (month_label,))
            cur.execute("INSERT INTO ref_structure(code,designation) VALUES('TEST','TEST')")
            sid = cur.lastrowid
            cur.execute("UPDATE carriere_situation SET structure_id=%s,structure_code='TEST',est_actuelle=1 WHERE id=1", (sid,))
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES"
                        "(1,'MILITAIRE','THIS MONTH','2026-09-10'),(1,'MILITAIRE','LATER','2026-10-01')")
    assert len(MySQLAgentRepository(settings).fetch_all()) == 1
    assert MySQLAgentRepository(settings).fetch_all()[0].diplome == 'THIS MONTH'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO sme_month_membership(agent_id,sme_periode_id,is_member,updated_at) VALUES(1,1,0,UTC_TIMESTAMP(6))')
    assert MySQLAgentRepository(settings).fetch_all() == []
    assert MySQLAgentRepository(settings).fetch_all_at_mois('2026-09') == []
    assert MySQLAvancementRepository(settings).load_agents(on=date(2026,9,30)) == []
    assert all(node.agent_count == 0 for node in MySQLOrganigramRepository(settings).fetch_organigram())
    with connect(**params) as conn:
        with conn.cursor() as cur:
            assert membership_projection_enabled(settings, cursor=cur)
            assert _fetch_liste_rows(cur, settings, export_mois='2026-09') == []
            sql, args = _select_liste(None, settings=settings, cursor=cur)
            cur.execute(sql, args)
            assert cur.fetchall() == ()
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=1 AND sme_periode_id=1')
            assert cur.fetchone()[0] == 1
            cur.execute('UPDATE sme_month_membership SET is_member=1 WHERE agent_id=1 AND sme_periode_id=1')
    assert len(MySQLAgentRepository(settings).fetch_all()) == 1


def change_fact(settings, aid, stamp, category='PERTE', *, fact_id=None, delete=False, since=None):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, MySQLConnection
    from sme_gend.services.membership_projection import apply_membership_for_agent
    # Match the real dated-movement service's _personnel_write transaction.
    with mysql_import_transaction(settings, read_committed=True):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                if delete:
                    cur.execute('DELETE FROM agent_effectif_movement WHERE id=%s', (fact_id,))
                elif fact_id is not None:
                    cur.execute('UPDATE agent_effectif_movement SET effective_date=%s,version=version+1 WHERE id=%s', (stamp, fact_id))
                else:
                    cur.execute('INSERT INTO agent_effectif_movement(agent_id,category,movement_kind,effective_date,'
                        'recorded_at,recorded_by) VALUES(%s,%s,\'TEST\',%s,UTC_TIMESTAMP(6),\'TEST\')', (aid, category, stamp))
                    fact_id = cur.lastrowid
        apply_membership_for_agent(settings, aid, since or stamp)
    return fact_id


def test_real_loss_gain_date_correction_and_delete_preserve_original_observations(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois) VALUES(2,'2026-10','2026-10-31')")
            cur.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,grade_libelle) VALUES(1,2,'MDL')")
    repo = MySQLAgentRepository(settings)
    loss = change_fact(settings, 1, date(2025,4,12))
    assert repo.fetch_all_at_mois('2026-09') == [] and repo.fetch_all_at_mois('2026-10') == []
    gain = change_fact(settings, 1, date(2026,10,3), 'GAIN')
    assert repo.fetch_all_at_mois('2026-09') == [] and len(repo.fetch_all_at_mois('2026-10')) == 1
    change_fact(settings, 1, date(2026,10,2), fact_id=loss, since=date(2025,4,12))
    assert len(repo.fetch_all_at_mois('2026-09')) == 1
    change_fact(settings, 1, date(2026,10,3), fact_id=gain, delete=True)
    assert repo.fetch_all_at_mois('2026-10') == []
    change_fact(settings, 1, date(2026,10,2), fact_id=loss, delete=True)
    assert len(repo.fetch_all_at_mois('2026-10')) == 1
    from sme_gend.services.membership_projection import validate_membership_snapshot
    assert validate_membership_snapshot(settings)['events'] == 0
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM sme_periode')
            assert cur.fetchone()[0] == 2  # No invented 2025 archive.
            cur.execute('SELECT COUNT(*) FROM carriere_situation')
            assert cur.fetchone()[0] == 2


def test_preparation_carries_loss_without_duplicating_facts(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    change_fact(settings, 1, date(2025,4,12))
    with publication_write_barrier(settings), period_state_write(settings, reason='test_membership'):
        prepare_month_projection(settings, '2026-10', '2026-09')
    assert MySQLAgentRepository(settings).fetch_all_at_mois('2026-10') == []
    from sme_gend.services.membership_projection import validate_membership_snapshot
    assert validate_membership_snapshot(settings) == {'events': 1, 'monthly_memberships': 2}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM agent_effectif_movement')
            assert cur.fetchone()[0] == 1
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=1')
            assert cur.fetchone()[0] == 2


def test_future_gain_materializes_existing_target_and_projects_its_dated_fields(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.month_projection import prepare_month_projection
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(2,'NEW GRADE','2026-10-05')")
            cur.execute("INSERT INTO agent_position_event(agent_id,position_admin,date_debut) VALUES(2,'Mission','2026-10-06')")
    change_fact(settings, 2, date(2026,10,3), 'GAIN')
    assert len(MySQLAgentRepository(settings).fetch_all()) == 1
    with publication_write_barrier(settings), period_state_write(settings, reason='test_membership'):
        prepare_month_projection(settings, '2026-10', '2026-09')
    row = next(row for row in MySQLAgentRepository(settings).fetch_all_at_mois('2026-10') if row.access_join_id == '43')
    assert row.grade == 'NEW GRADE' and row.position_admin == 'Mission'
    from sme_gend.services.membership_projection import validate_membership_snapshot
    assert validate_membership_snapshot(settings) == {'events': 1, 'monthly_memberships': 1}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=2')
            assert cur.fetchall() == (('2026-10',),)


def test_reimport_rebuilds_membership_from_new_observation_and_preserves_real_timeline(isolated_mysql, monkeypatch, tmp_path):
    from dataclasses import replace
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    repo = MySQLAgentRepository(settings)
    observed = replace(repo.fetch_all()[0], id='42')
    loss = change_fact(settings, 1, date(2025,4,12))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO import_lot(id,sme_periode_id,chemin_fichier) VALUES(1,1,'SYNTHETIC')")
    repo.bulk_import_from_access_rows([], sme_periode_id=1, import_lot_id=1, mois='2026-09')
    change_fact(settings, 1, date(2025,4,12), fact_id=loss, delete=True)
    assert repo.fetch_all() == []  # The empty replacement is the new observation.
    repo.bulk_import_from_access_rows([observed], sme_periode_id=1, import_lot_id=1, mois='2026-09')
    assert len(repo.fetch_all()) == 1
    from sme_gend.services.membership_projection import validate_membership_snapshot
    assert validate_membership_snapshot(settings)['events'] == 0
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM sme_periode')
            assert cur.fetchone()[0] == 1


def test_reimport_overlay_failure_rolls_back_replacement_and_membership(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    from sme_gend.services import membership_projection as membership
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    repo = MySQLAgentRepository(settings)
    change_fact(settings, 1, date(2025,4,12))
    original = membership.rebuild_imported_membership
    def failing(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic overlay failure')
    monkeypatch.setattr(membership, 'rebuild_imported_membership', failing)
    with pytest.raises(RuntimeError, match='overlay failure'):
        repo.bulk_import_from_access_rows([], sme_periode_id=1, import_lot_id=1, mois='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=1 AND sme_periode_id=1')
            assert cur.fetchone()[0] == 1
            cur.execute('SELECT is_member,baseline_member FROM sme_month_membership WHERE agent_id=1 AND sme_periode_id=1')
            assert cur.fetchone() == (0,1)


def test_reimport_other_agents_cannot_resurrect_a_cancelled_materialized_gain(isolated_mysql, monkeypatch, tmp_path):
    from dataclasses import replace
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    repo = MySQLAgentRepository(settings)
    observed = replace(repo.fetch_all()[0], id='42')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=1")
    gain = change_fact(settings, 2, date(2026,9,10), 'GAIN')
    assert len(repo.fetch_all()) == 2
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT est_actuelle FROM carriere_situation WHERE agent_id=2 AND sme_periode_id=1')
            assert cur.fetchone()[0] == 1
    change_fact(settings, 2, date(2026,9,10), fact_id=gain, delete=True)
    assert [row.access_join_id for row in repo.fetch_all()] == ['42']
    repo.bulk_import_from_access_rows([observed], sme_periode_id=1, import_lot_id=None, mois='2026-09')
    assert [row.access_join_id for row in repo.fetch_all()] == ['42']
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=2 AND sme_periode_id=1')
            assert cur.fetchone()[0] == 0
            cur.execute('SELECT COUNT(*) FROM sme_month_membership WHERE agent_id=2 AND sme_periode_id=1 AND is_member=1')
            assert cur.fetchone()[0] == 0


def test_legacy_fill_and_closure_refuse_real_facts_even_before_document_activation(isolated_mysql, monkeypatch, tmp_path):
    from unittest.mock import Mock
    from sme_gend.services import month_fill, document_month_cycle as cycle, sme_month_closure as closure
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(month_fill, 'can_fill_month', lambda: True)
    plan = month_fill.preview_month_fill(settings, '2026-09', '2026-10')
    assert not plan.conflicts
    change_fact(settings, 1, date(2025,4,12))
    assert cycle.get_cycle_mode(settings) == cycle.LEGACY
    backup = Mock(side_effect=AssertionError('The incompatible plan must fail before backup'))
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup', backup)
    for operation in (
        lambda: month_fill.list_month_sources(settings),
        lambda: month_fill.preview_month_fill(settings, '2026-09', '2026-10'),
        lambda: month_fill.apply_month_fill(settings, plan),
        lambda: closure.run_piste_a_month_closure(settings, '2026-09', '2026-10'),
        lambda: closure._commit_closure_transition(settings, '2026-09', '2026-10', action='TEST', audit_payload={}),
    ):
        with pytest.raises(cycle.LegacyCycleDisabledError, match='cycle documentaire'):
            operation()
    backup.assert_not_called()
    monkeypatch.setattr(closure, 'append_audit_event', Mock())
    result = closure.import_existing_db_for_month(settings, tmp_path / 'unused.mdb', '2025-04',
        import_to_mysql=False, archive_and_close=False)
    assert result.tabsme_rows_closed == 0
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM sme_periode')
            assert cur.fetchone()[0] == 1


def test_document_cycle_refuses_classic_fill_even_without_dated_movements(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services import month_fill, document_month_cycle as cycle
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(month_fill, 'can_fill_month', lambda: True)
    plan = month_fill.preview_month_fill(settings, '2026-09', '2026-10')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO app_config(config_key,payload) VALUES(%s,%s)',
                (cycle.KEY_CYCLE_MODE, '{"mode":"publications"}'))
    for operation in (lambda: month_fill.preview_month_fill(settings, '2026-09', '2026-10'),
                      lambda: month_fill.apply_month_fill(settings, plan)):
        with pytest.raises(cycle.LegacyCycleDisabledError, match='cycle documentaire'):
            operation()


def test_first_dated_loss_waits_for_legacy_copy_then_covers_new_target(isolated_mysql, monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sme_gend.services import month_fill
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings, connect, params, _, _ = migrated(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(month_fill, 'can_fill_month', lambda: True)
    plan = month_fill.preview_month_fill(settings, '2026-09', '2026-10')
    backup = tmp_path / 'synthetic-before-fill.zip'
    backup.write_bytes(b'SYNTHETIC')
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup', lambda *args, **kwargs: backup)
    entered, release, fact_done = Event(), Event(), Event()
    original = month_fill._read_state
    def read(*args, **kwargs):
        if kwargs.get('lock'):
            entered.set()
            assert release.wait(timeout=15)
        return original(*args, **kwargs)
    monkeypatch.setattr(month_fill, '_read_state', read)
    def lose():
        result = change_fact(settings, 1, date(2026,9,12))
        fact_done.set()
        return result
    with ThreadPoolExecutor(max_workers=2) as workers:
        copying = workers.submit(month_fill.apply_month_fill, settings, plan)
        assert entered.wait(timeout=15)
        loss = workers.submit(lose)
        try:
            assert not fact_done.wait(timeout=.15)
        finally:
            release.set()
        assert copying.result(timeout=30)['target'] == '2026-10'
        loss.result(timeout=30)
    assert MySQLAgentRepository(settings).fetch_all_at_mois('2026-10') == []
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=1')
            assert cur.fetchone()[0] == 2
            cur.execute('SELECT COUNT(*) FROM sme_month_membership WHERE agent_id=1 AND is_member=0')
            assert cur.fetchone()[0] == 2
