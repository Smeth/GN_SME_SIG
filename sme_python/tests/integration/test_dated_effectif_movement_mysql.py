"""Real movement dates on the explicitly disposable MySQL instance only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from test_restore_mysql import isolated_mysql
from test_publication_registry_mysql import migrate
from sme_gend.services import dated_effectif_movement as movement


def test_real_date_without_archive_and_cas_journal_are_atomic(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    original = movement.create_movement(settings, 1, 'PERTE', 'RETRAITE', '2025-02-05',
                                        reason='Document historique synthétique')
    assert original.effective_date == date(2025,2,5) and original.reference_note is None
    assert original.recorded_at and original.recorded_by and original.agent_id==1
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT annee_mois FROM sme_periode ORDER BY id')
            assert cursor.fetchall()==(('2026-11',),)
            cursor.execute("SELECT effective_from,actor,reason,source_table FROM sme_temporal_change WHERE source_table='agent_effectif_movement'")
            result = cursor.fetchone()
            assert result[0]==date(2025,2,5) and result[1] and result[2]=='Document historique synthétique'
    corrected = movement.update_movement(settings, original, category='GAIN', movement_kind='RÉINTÉGRATION',
        effective_date='2026-11-01', reference_note='NOTE 2026/1', reason='Date et mouvement régularisés')
    assert corrected.version==2 and corrected.effective_date==date(2026,11,1)
    with pytest.raises(movement.MovementConflict):
        movement.delete_movement(settings, original, reason='Version périmée')
    assert movement.list_movements(settings,month='2026-11')==[corrected]
    adapter = movement.export_month_movements(settings,'2026-11')
    assert len(adapter)==1 and adapter[0].date_reference=='2026-11-01'
    assert adapter[0].grade=='CNE' and adapter[0].structure_code=='DPF'
    assert adapter[0].movement_id is None and adapter[0].write_token is None and adapter[0].id=='42'
    movement.delete_movement(settings, corrected, reason='Pièce retirée après vérification')
    assert movement.list_movements(settings)==[]
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT mutation,effective_from FROM sme_temporal_change WHERE source_table='agent_effectif_movement' ORDER BY id")
            assert cursor.fetchall()==(('create',date(2025,2,5)),('update',date(2025,2,5)),('delete',date(2026,11,1)))
            cursor.execute('SELECT COUNT(*) FROM mouvement_effectif')
            assert cursor.fetchone()[0]==0


def test_opposite_movements_same_agent_day_refuse_without_order_invention(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    movement.create_movement(settings,1,'GAIN','AJOUT','2026-11-01')
    with pytest.raises(movement.MovementConflict,match='ambigus'):
        movement.create_movement(settings,1,'PERTE','RETRAITE','2026-11-01')
    assert len(movement.list_movements(settings))==1
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sme_temporal_change WHERE source_table='agent_effectif_movement'")
            assert cursor.fetchone()[0]==1


def test_failure_of_projection_rolls_back_movement_and_source_journal(isolated_mysql,tmp_path,monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql,tmp_path,monkeypatch)
    def fail(*args): raise RuntimeError('Synthetic projection failure')
    monkeypatch.setattr('sme_gend.services.membership_projection.apply_membership_for_agent',fail)
    with pytest.raises(RuntimeError,match='Synthetic projection'):
        movement.create_movement(settings,1,'PERTE','RETRAITE','2026-11-01')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent_effectif_movement')
            assert cursor.fetchone()[0]==0
            cursor.execute("SELECT COUNT(*) FROM sme_temporal_change WHERE source_table='agent_effectif_movement'")
            assert cursor.fetchone()[0]==0


def test_picker_resolves_old_alias_to_canonical_agent(isolated_mysql,tmp_path,monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql,tmp_path,monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO agent_identity_alias(alias_legacy_id,canonical_agent_id,source_agent_id,source_snapshot,merged_by,reason) "
                           "VALUES('OLD-42',1,99,'{}','synthetic','fixture alias')")
    assert movement.resolve_selected_agent(settings,'OLD-42')==1
    assert movement.resolve_selected_agent(settings,'42')==1


def test_two_clients_same_day_only_one_commits(isolated_mysql,tmp_path,monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql,tmp_path,monkeypatch)
    def create(category):
        try:
            return movement.create_movement(settings,1,category,'TEST CONCURRENCE','2026-11-01').category
        except movement.MovementConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create,['GAIN','PERTE']))
    assert outcomes.count('conflict')==1 and len(movement.list_movements(settings))==1
