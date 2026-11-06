"""Reprise sur MySQL jetable, sans accès aux données de travail."""
from dataclasses import replace
from datetime import date
import pytest
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database
from sme_gend.services import month_fill as service


@pytest.fixture
def fill_database(alert_database, tmp_path, monkeypatch):
    settings, connect, params = alert_database
    from sme_gend.services.mysql_import_period import apply_agent_conge_schema
    from sme_gend.services.position_admin_events import apply_position_event_schema
    apply_agent_conge_schema(settings)
    apply_position_event_schema(settings)
    backup = tmp_path / 'before_fill.zip'
    backup.write_bytes(b'SYNTHETIC BACKUP')
    monkeypatch.setattr(service, 'can_fill_month', lambda: True)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup', lambda *a, **kw: backup)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois,statut) VALUES (1,'2026-09','2026-09-30','Ouvert')")
            cur.executemany("INSERT INTO agent (id,legacy_access_id,noms) VALUES (%s,%s,%s)",
                            [(i, str(100+i), f'SYNTHETIC AGENT {i}') for i in range(2, 8)])
            positions = ['Présent', 'Mission', 'Présent', 'StageEx', 'Malade', 'CongéAdmin', 'Punis']
            cur.executemany("INSERT INTO carriere_situation (agent_id,sme_periode_id,est_actuelle,grade_libelle,structure_code,fonction_libelle,diplome_libelle,date_obtention,position_admin_libelle,date_debut,date_fin) VALUES (%s,1,1,'LT','A','Chef','BIA','2020-01-01',%s,'2026-09-01',NULL)",
                            list(enumerate(positions, start=1)))
            cur.execute("INSERT INTO mouvement_effectif (agent_id,sme_periode_id,categorie_mouvement,motif) VALUES (3,1,'PERTE','RETRAITE')")
            cur.execute("INSERT INTO ref_type_stage (code,libelle) VALUES ('EXTERIEUR','Stage extérieur') ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)")
            cur.execute("INSERT INTO parcours_stage (agent_id,sme_periode_id,type_stage_code,date_debut,date_fin,nature_libelle,lieu,observation) VALUES (4,1,'EXTERIEUR','2026-09-01','2026-10-31','CDS','Turquie','2026'),(4,1,'EXTERIEUR','2026-09-01','2026-09-30','TERMINE','Turquie','terminé')")
            cur.execute("INSERT INTO agent_mission (agent_id,sme_periode_id,date_debut,date_fin,nature_libelle) VALUES (2,1,'2026-09-15','2026-10-15','RENFORT')")
            cur.execute("INSERT INTO agent_malade (agent_id,sme_periode_id,date_debut,pathologie) VALUES (5,1,'2026-09-18','SYNTHETIC')")
            cur.execute("INSERT INTO agent_conge (agent_id,sme_periode_id,type_conge_code,date_debut,date_fin,quota_jours,source_code) VALUES (6,1,'ADMIN','2026-09-25','2026-10-05',11,'saisie')")
            cur.execute("INSERT INTO agent_diplome (agent_id,libelle_libre,date_obtention,obs) VALUES (1,'BIA','2020-01-01','Import archive 2026-09')")
            cur.execute("INSERT INTO agent_affectation (agent_id,structure_code,date_affectation,sme_periode_mois,obs) VALUES (1,'A','2026-09-01','2026-09','Import archive 2026-09')")
            cur.execute("INSERT INTO agent_passation_grade (agent_id,grade_libelle,date_passation,obs) VALUES (1,'LT','2020-01-01','Import archive 2026-09')")
    return settings, connect, params


def snapshot_source(connect, params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            rows = {}
            for table in service.MONTH_TABLES:
                cur.execute(f'SELECT * FROM {table} WHERE sme_periode_id=1 ORDER BY id')
                rows[table] = cur.fetchall()
            return rows


@pytest.mark.parametrize('activities', [False, True])
def test_fill_preserves_source_identities_history_and_active_month(fill_database, activities):
    settings, connect, params = fill_database
    before = snapshot_source(connect, params)
    options = service.FillOptions(activities, activities, activities, activities)
    plan = service.preview_month_fill(settings, '2026-09', '2026-10', options)
    assert not plan.conflicts
    assert plan.excluded == 1
    assert dict(plan.counts)['carriere_situation'] == 6
    assert len(plan.review) == (1 if activities else 5)
    assert dict(plan.counts)['parcours_stage'] == int(activities)
    steps = []
    result = service.apply_month_fill(settings, plan, progress=steps.append)
    assert len(steps) == service.month_fill_progress_steps(plan)
    assert 'validé en base' in steps[-1]
    assert result['target'] == '2026-10'
    assert snapshot_source(connect, params) == before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois')
            assert cur.fetchall() == (('2026-09', 'Ouvert'), ('2026-10', 'Préparé'))
            cur.execute('SELECT COUNT(*) FROM agent')
            assert cur.fetchone()[0] == 7
            for table in ('agent_diplome', 'agent_affectation', 'agent_passation_grade'):
                cur.execute(f'SELECT COUNT(*) FROM {table}')
                assert cur.fetchone()[0] == 1
            cur.execute('SELECT agent_id,date_debut,date_fin,est_actuelle,import_lot_id FROM carriere_situation WHERE sme_periode_id<>1 ORDER BY agent_id')
            rows = cur.fetchall()
            assert [r[0] for r in rows] == [1, 2, 4, 5, 6, 7]
            assert all(r[1:] == (date(2026, 10, 1), date(2026, 10, 31), 0, None) for r in rows)
            for table in service.ACTIVITIES:
                cur.execute(f'SELECT source_ligne_legacy,import_lot_id FROM {table} WHERE sme_periode_id<>1')
                copies = cur.fetchall()
                assert len(copies) == int(activities)
                assert all(':saisie:r:' in r[0] and r[1] is None for r in copies)
            cur.execute("SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id<>1 AND position_admin_libelle='À vérifier'")
            assert cur.fetchone()[0] == len(plan.review)
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_fill'")
            assert cur.fetchone()[0] == 1
    # Un second clic ne remplit pas deux fois la période.
    with pytest.raises(ValueError, match='aperçu|changé'):
        service.apply_month_fill(settings, plan)


@pytest.mark.parametrize('change', ['source', 'target', 'period'])
def test_stale_preview_is_rejected_without_partial_creation(fill_database, change):
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if change == 'source':
                cur.execute("UPDATE carriere_situation SET fonction_libelle='Adjoint' WHERE agent_id=1")
            elif change == 'period':
                cur.execute("UPDATE sme_periode SET statut='Réouvert' WHERE id=1")
            else:
                cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-10','2026-10-31','Préparé')")
                cur.execute("INSERT INTO agent_mission (agent_id,sme_periode_id,nature_libelle) VALUES (1,2,'NE PAS ECRASER')")
    with pytest.raises(ValueError, match='changé'):
        service.apply_month_fill(settings, plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id<>1')
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='sme_month_fill'")
            assert cur.fetchone()[0] == 0
            if change == 'target':
                cur.execute('SELECT nature_libelle FROM agent_mission WHERE sme_periode_id=2')
                assert cur.fetchone()[0] == 'NE PAS ECRASER'


def test_audit_failure_rolls_back_all_copies_and_period(fill_database, monkeypatch):
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10', service.FillOptions(True, True, True, True))
    before = snapshot_source(connect, params)
    def fail(*a, **kw):
        raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr('sme_gend.services.business_audit.record_business_action', fail)
    steps = []
    with pytest.raises(RuntimeError, match='audit'):
        service.apply_month_fill(settings, plan, progress=steps.append)
    assert len(steps) < service.month_fill_progress_steps(plan)
    assert not any('validé en base' in step for step in steps)
    assert snapshot_source(connect, params) == before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM sme_periode WHERE annee_mois='2026-10'")
            assert cur.fetchone()[0] == 0
            for table in service.MONTH_TABLES:
                cur.execute(f'SELECT COUNT(*) FROM {table} WHERE sme_periode_id<>1')
                assert cur.fetchone()[0] == 0


def test_closed_or_nonempty_month_is_blocked_and_wrong_database_refused(fill_database):
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    with pytest.raises(ValueError, match='contexte'):
        service.apply_month_fill(replace(settings, mysql_database='different_database'), plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-10','2026-10-31','Fermé')")
    blocked = service.preview_month_fill(settings, '2026-09', '2026-10')
    assert any('fermé' in msg for msg in blocked.conflicts)
    with pytest.raises(ValueError, match='conflits'):
        service.apply_month_fill(settings, blocked)


def test_backup_failure_prevents_any_insertion(fill_database, monkeypatch):
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='sauvegarde'):
        service.apply_month_fill(settings, plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM sme_periode')
            assert cur.fetchone()[0] == 1


def test_two_simultaneous_fills_cannot_duplicate_target(fill_database, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    settings, connect, params = fill_database
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    from sme_gend.services import backup_restore_service
    original_backup = backup_restore_service.ensure_recent_backup
    gate = Barrier(2)
    def synchronized_backup(*args, **kwargs):
        result = original_backup(*args, **kwargs)
        gate.wait(timeout=10)
        return result
    monkeypatch.setattr(backup_restore_service, 'ensure_recent_backup', synchronized_backup)
    def fill():
        try:
            service.apply_month_fill(settings, plan)
            return 'ok'
        except ValueError:
            return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: fill(), range(2)))
    assert sorted(results) == ['ok', 'stale']
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id<>1')
            assert cur.fetchone()[0] == 6


@pytest.mark.parametrize('target_status', ['Ouvert', 'Réouvert'])
def test_empty_existing_target_keeps_its_status_and_current_flags_are_coherent(fill_database, target_status):
    settings, connect, params = fill_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            if target_status == 'Ouvert':
                cur.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=1")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-10','2026-10-31',%s)", (target_status,))
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    assert not plan.conflicts
    service.apply_month_fill(settings, plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode WHERE id=2')
            assert cur.fetchone()[0] == target_status
            cur.execute('SELECT DISTINCT est_actuelle,date_fin FROM carriere_situation WHERE sme_periode_id=2')
            expected = ((1, None),) if target_status == 'Ouvert' else ((0, date(2026, 10, 31)),)
            assert cur.fetchall() == expected
            if target_status == 'Ouvert':
                cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1 AND est_actuelle=1')
                assert cur.fetchone()[0] == 0


def test_gain_after_loss_in_source_month_is_not_treated_as_final_departure(fill_database):
    settings, connect, params = fill_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE mouvement_effectif SET date_reference='2026-09-01' WHERE agent_id=3")
            cur.execute("INSERT INTO mouvement_effectif (agent_id,sme_periode_id,categorie_mouvement,date_reference) VALUES (3,1,'GAIN','2026-09-20')")
    plan = service.preview_month_fill(settings, '2026-09', '2026-10')
    assert plan.excluded == 0
    assert dict(plan.counts)['carriere_situation'] == 7
