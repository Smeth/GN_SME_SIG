"""Modes de réimport sur MySQL jetable ; lecture Access simulée uniquement."""
import pytest
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database
from test_month_fill_mysql import fill_database
from sme_gend.services import import_replacement as service
from sme_gend.services import mysql_bundle_import as bundle
from sme_gend.repositories.mysql_connection import MySQLConnection


@pytest.fixture
def replacement_database(fill_database, monkeypatch):
    settings, connect, params = fill_database
    monkeypatch.setattr(service, 'authorize_replacement', lambda preserve: None)
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema', lambda _: None)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready', lambda _: None)
    class Access:
        def fetch_all(self):
            return []
    monkeypatch.setattr('sme_gend.repositories.liste_generale_access_repository.AccessListeGeneraleRepository', lambda _: Access())
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO import_lot (id,sme_periode_id) VALUES (10,1)')
            cur.execute('UPDATE carriere_situation SET import_lot_id=10 WHERE agent_id=1')
            cur.execute("INSERT INTO parcours_stage (agent_id,sme_periode_id,type_stage_code,date_debut,date_fin,nature_libelle,source_ligne_legacy,import_lot_id) VALUES (4,1,'EXTERIEUR','2026-09-01','2026-09-30','IMPORTE','104',10)")
            cur.execute("INSERT INTO sme_periode (id,annee_mois,date_dernier_jour_mois,statut) VALUES (2,'2026-10','2026-10-31','Préparé')")
            cur.execute("INSERT INTO carriere_situation (agent_id,sme_periode_id,diplome_libelle,date_obtention,structure_code,grade_libelle,date_grade) VALUES (1,2,'BIA','2020-01-01','A','LT','2020-01-01')")
            cur.execute("INSERT INTO workflow_task (mois,task_kind,title,detail) VALUES ('2026-09','manual','SOURCE','SYNTHETIC'),('2026-10','manual','AUTRE MOIS','SYNTHETIC')")
            cur.execute("INSERT INTO career_alert_ack (agent_legacy_id,alert_code,mois) VALUES ('101','TEST','2026-09')")
    return settings, connect, params


def bulk_load(settings, rows):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    repo = MySQLAgentRepository.__new__(MySQLAgentRepository)
    repo._settings = settings
    repo._connect = lambda **kw: MySQLConnection(settings).connect()
    return repo.bulk_import_from_access_rows(rows, sme_periode_id=1, import_lot_id=10, mois='2026-09')


@pytest.mark.parametrize('preserve', [True, False])
def test_bundle_mode_cleans_selected_scope_then_mirrors_file_without_new_identities(replacement_database, monkeypatch, preserve):
    from sme_gend.models.liste_generale import ListeGeneraleRow
    settings, connect, params = replacement_database
    preview = service.preview_import_replacement(settings, '2026-09', preserve_manual=preserve)
    assert dict(preview.counts)['parcours_stage'] == (1 if preserve else 3)
    assert dict(preview.counts)['carriere_situation'] == 7
    assert ('workflow_task' in dict(preview.counts)) is not preserve
    def run(access, target, **kw):
        assert kw.get('preserve_manual', True) is preserve
        counts = bundle.replace_import_month_data(access, target, '2026-09', preserve_manual=preserve)
        incoming = ListeGeneraleRow.from_sequence(('101', 'SYNTHETIC FILE AGENT'))
        bulk_load(settings, [incoming])
        return dict(month_replacement=counts)
    monkeypatch.setattr(bundle, '_run_mysql_bundle_import', run)
    bundle.import_full_mysql_bundle_from_access(settings, settings, mois='2026-09',
                                               preserve_manual=preserve, replacement_preview=preview)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT agent_id FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchall() == ((1,),)
            cur.execute('SELECT COUNT(*) FROM parcours_stage WHERE sme_periode_id=1')
            assert cur.fetchone()[0] == (2 if preserve else 0)
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE mois='2026-09'")
            assert cur.fetchone()[0] == int(preserve)
            cur.execute("SELECT COUNT(*) FROM career_alert_ack WHERE mois='2026-09'")
            assert cur.fetchone()[0] == int(preserve)
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE mois='2026-10'")
            assert cur.fetchone()[0] == 1
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=2')
            assert cur.fetchone()[0] == 1
            cur.execute('SELECT COUNT(*) FROM agent')
            assert cur.fetchone()[0] == 7
            # Le diplôme reste attesté en octobre malgré le remplacement de septembre.
            cur.execute('SELECT obs FROM agent_diplome')
            assert cur.fetchone()[0] == 'Import archive 2026-10'


@pytest.mark.parametrize('failure', ['after_purge', 'unreadable_source', 'stale_preview'])
def test_replacement_failure_never_leaves_month_empty(replacement_database, monkeypatch, failure):
    settings, connect, params = replacement_database
    preview = service.preview_import_replacement(settings, '2026-09', preserve_manual=False)
    if failure == 'stale_preview':
        with connect(**params) as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE parcours_stage SET observation='NEW EDIT' WHERE id=(SELECT x.id FROM (SELECT MIN(id) id FROM parcours_stage) x)")
    if failure == 'unreadable_source':
        class BadAccess:
            def fetch_all(self):
                raise RuntimeError('synthetic unreadable source')
        monkeypatch.setattr('sme_gend.repositories.liste_generale_access_repository.AccessListeGeneraleRepository', lambda _: BadAccess())
    def run(access, target, **kw):
        bundle.replace_import_month_data(access, target, '2026-09', preserve_manual=False)
        raise RuntimeError('synthetic failure after purge')
    monkeypatch.setattr(bundle, '_run_mysql_bundle_import', run)
    with pytest.raises((RuntimeError, ValueError)):
        bundle.import_full_mysql_bundle_from_access(settings, settings, mois='2026-09',
                                                   preserve_manual=False, replacement_preview=preview)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0] == 7
            cur.execute('SELECT COUNT(*) FROM parcours_stage WHERE sme_periode_id=1')
            assert cur.fetchone()[0] == 3
            cur.execute("SELECT COUNT(*) FROM workflow_task WHERE mois='2026-09'")
            assert cur.fetchone()[0] == 1


def test_valid_empty_file_also_clears_carried_monthly_list(replacement_database):
    from sme_gend.services.active_month_guard import period_state_write
    settings, connect, params = replacement_database
    with period_state_write(settings, reason='synthetic_import', mois='2026-09'):
        bulk_load(settings, [])
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0] == 0
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=2')
            assert cur.fetchone()[0] == 1


def test_preview_counts_manual_events_even_before_period_exists(replacement_database, monkeypatch):
    from sme_gend.services.mysql_import_period import ensure_sme_periode
    settings, connect, params = replacement_database
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome (agent_id,libelle_libre,date_obtention,obs) VALUES (1,'MANUEL NOVEMBRE','2026-11-15','Saisie applicative')")
    preview = service.preview_import_replacement(settings, '2026-11', preserve_manual=False)
    assert dict(preview.counts)['agent_diplome'] == 1
    kept = service.preview_import_replacement(settings, '2026-11', preserve_manual=True)
    assert dict(kept.counts)['agent_diplome'] == 0
    def run(access, target, **kw):
        with MySQLConnection(settings).connect() as conn:
            ensure_sme_periode(conn, '2026-11', statut='Fermé')
        counts = bundle.replace_import_month_data(access, target, '2026-11', preserve_manual=False)
        assert counts['agent_diplome'] == 1
        return {}
    monkeypatch.setattr(bundle, '_run_mysql_bundle_import', run)
    bundle.import_full_mysql_bundle_from_access(settings, settings, mois='2026-11',
                                               preserve_manual=False, replacement_preview=preview)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_diplome WHERE libelle_libre='MANUEL NOVEMBRE'")
            assert cur.fetchone()[0] == 0
