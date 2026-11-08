from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def month_service(isolated_mysql,monkeypatch,tmp_path,*,archive=False,legacy=False):
    from sme_gend.services import empty_sme_month as module
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.load_sme_context',lambda:SimpleNamespace(mois_actif='2026-10' if archive else '2026-09'))
    monkeypatch.setattr('sme_gend.config.sme_context.is_mois_ferme',lambda month:archive)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:True)
    from sme_gend.services import backup_restore_service as backup
    monkeypatch.setattr(backup,'_collect_config_paths',lambda:[])
    monkeypatch.setattr(backup,'_collect_personnel_media_roots',lambda *a:[])
    monkeypatch.setattr(backup,'project_root',lambda:tmp_path)
    def safety(settings):
        destination=tmp_path/'backup.zip'
        backup.create_full_backup(settings,destination)
        return destination
    monkeypatch.setattr(backup,'create_backup_to_default_dir',safety)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready',lambda *a:None)
    monkeypatch.setattr('sme_gend.services.sme_month_closure.sme_archives_dir',lambda:tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET date_debut='2026-09-01',date_fin=NULL,est_actuelle=1 WHERE id=1")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-08','2026-08-31','Ferm\u00e9')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,date_debut,date_fin,est_actuelle) VALUES(2,1,2,'OLD AUGUST','2026-08-01',NULL,1)")
            if legacy:cur.execute("UPDATE sme_periode SET annee_mois='202609' WHERE id=1")
    return module,settings,connect,params


@pytest.mark.parametrize('archive',[False,True])
def test_full_empty_does_not_display_another_month(isolated_mysql,monkeypatch,tmp_path,archive):
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    module,settings,connect,params=month_service(isolated_mysql,monkeypatch,tmp_path,archive=archive)
    result=module.empty_sme_month(settings,'2026-09',imported_only=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]==0
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=2')
            assert cur.fetchone()[0]==1
    assert MySQLAgentRepository(replace(settings,mysql_view_mois='2026-09')).fetch_all()==[]
    if not archive:
        assert MySQLAgentRepository(replace(settings,mysql_view_mois=None)).fetch_all()==[]


def test_full_empty_matches_legacy_month_label(isolated_mysql,monkeypatch,tmp_path):
    module,settings,connect,params=month_service(isolated_mysql,monkeypatch,tmp_path,legacy=True)
    result=module.empty_sme_month(settings,'2026-09',imported_only=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]==0


def test_full_empty_covers_duplicate_month_labels(isolated_mysql,monkeypatch,tmp_path):
    module,settings,connect,params=month_service(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(3,'202609','2026-09-30','Ouvert')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,est_actuelle) VALUES(3,1,3,'DUPLICATE',1)")
    preview=module.preview_empty_sme_month(settings,'2026-09',imported_only=False)
    assert preview['carriere_situation']==2
    result=module.empty_sme_month(settings,'2026-09',imported_only=False)
    assert result['carriere_situation']==2
    assert not any(result['remaining_month_rows'].values())
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT sme_periode_id FROM carriere_situation')
            assert cur.fetchall()==((2,),)


def test_full_empty_rejects_false_success(isolated_mysql,monkeypatch,tmp_path):
    module,settings,connect,params=month_service(isolated_mysql,monkeypatch,tmp_path)
    monkeypatch.setattr(module,'purge_sme_periode_for_reimport',lambda *a,**kw:{})
    with pytest.raises(RuntimeError,match='Vidage annul'):
        module.empty_sme_month(settings,'2026-09',imported_only=False)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]==1


@pytest.mark.parametrize('imported_only',[False,True])
def test_real_dated_membership_refuses_preview_and_delete_before_backup(isolated_mysql,monkeypatch,tmp_path,imported_only):
    from test_publication_registry_mysql import migrate
    from sme_gend.services import empty_sme_month as module
    from sme_gend.services.dated_effectif_movement import create_movement
    settings,connect,params,_=migrate(isolated_mysql,tmp_path,monkeypatch)
    create_movement(settings,1,'PERTE','SYNTHETIC RETIREMENT','2025-02-05',reason='Synthetic dated evidence')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sme_month_membership ORDER BY agent_id,sme_periode_id')
            before=cursor.fetchall()
            assert before and before[0][2]==0
    def forbidden(*a,**kw):raise AssertionError('No backup or schema write on refused deletion')
    monkeypatch.setattr('sme_gend.services.backup_restore_service.create_backup_to_default_dir',forbidden)
    monkeypatch.setattr('sme_gend.repositories.mysql_common.ensure_mysql_schema',forbidden)
    for function in (module.preview_empty_sme_month,module.empty_sme_month):
        with pytest.raises(ValueError,match='réellement datés'):
            function(settings,'2026-11',imported_only=imported_only)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sme_month_membership ORDER BY agent_id,sme_periode_id')
            assert cursor.fetchall()==before
            cursor.execute('SELECT COUNT(*) FROM agent_effectif_movement')
            assert cursor.fetchone()[0]==1
