import json
from pathlib import Path
import pytest
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services
from sme_gend.services.month_backup import create_month_backup,restore_month_backup,inspect_month_backup


def setup(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import backup_restore_service as backup
    settings,connect,params,_,_=setup_services(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'test_admin')
    monkeypatch.setattr(backup,'_collect_config_paths',lambda:[])
    monkeypatch.setattr(backup,'_collect_personnel_media_roots',lambda s:[])
    def safety(s):
        path=tmp_path/'safety.zip';backup.create_full_backup(s,path);return path
    monkeypatch.setattr(backup,'create_backup_to_default_dir',safety)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO app_config(config_key,payload) VALUES('auth_config','{\"sentinel\":true}') ON DUPLICATE KEY UPDATE payload=VALUES(payload)")
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(2,'2026-08','2026-08-31','Ferm\u00e9')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle) VALUES(2,1,2,'OTHER MONTH')")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'SEPT GRADE','2026-09-15')")
            cur.execute("INSERT INTO workflow_task(mois,task_kind,title) VALUES('2026-09','test','MONTH TASK'),('2026-08','test','OTHER TASK')")
    return settings,connect,params


def snapshot(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM carriere_situation WHERE sme_periode_id=2')
            other=cur.fetchall()
            cur.execute("SELECT * FROM workflow_task WHERE mois='2026-08'")
            tasks=cur.fetchall()
            cur.execute("SELECT payload FROM app_config WHERE config_key='auth_config'")
            auth=cur.fetchone()
    return other,tasks,auth


def test_round_trip_replaces_only_selected_month(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    before=snapshot(connect,params)
    path=tmp_path/'month.zip'
    result=create_month_backup(settings,'2026-09',path)
    payload=inspect_month_backup(path)
    assert payload['month']=='2026-09'
    assert len(payload['tables']['carriere_situation'])==1
    assert 'app_config' not in payload['tables']
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET grade_libelle='BROKEN' WHERE sme_periode_id=1")
            cur.execute("UPDATE agent SET noms='CURRENT IDENTITY' WHERE id=1")
            cur.execute("DELETE FROM agent_passation_grade WHERE date_passation='2026-09-15'")
            cur.execute("DELETE FROM workflow_task WHERE mois='2026-09'")
    restored=restore_month_backup(settings,path,confirmed_month='2026-09')
    assert Path(restored['backup']).is_file()
    assert snapshot(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]!='BROKEN'
            cur.execute("SELECT noms FROM agent WHERE id=1")
            assert cur.fetchone()[0]=='CURRENT IDENTITY'
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE grade_libelle='SEPT GRADE'")
            assert cur.fetchone()[0]==1
            cur.execute("SELECT title FROM workflow_task WHERE mois='2026-09'")
            assert cur.fetchone()[0]=='MONTH TASK'
    # A second restore must not duplicate monthly events or agents.
    restore_month_backup(settings,path,confirmed_month='2026-09')
    assert snapshot(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE grade_libelle='SEPT GRADE'")
            assert cur.fetchone()[0]==1


def test_restore_remaps_colliding_agent_ids(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    payload=inspect_month_backup(path)
    legacy=payload['tables']['agent'][0]['legacy_access_id']
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET legacy_access_id='DIFFERENT',matricule_militaire=NULL,matricule_solde=NULL WHERE id=1")
            cur.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES(99,%s,'EXISTING SAME PERSON')",(legacy,))
    before=snapshot(connect,params)
    restore_month_backup(settings,path,confirmed_month='2026-09')
    assert snapshot(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT agent_id FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]==99
            cur.execute("SELECT noms FROM agent WHERE id=99")
            assert cur.fetchone()[0]=='EXISTING SAME PERSON'


def test_month_restore_audit_failure_rolls_back(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET grade_libelle='KEEP ON FAILURE' WHERE sme_periode_id=1")
    monkeypatch.setattr(AppConfigRepository,'append_audit',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('AUDIT FAILURE')))
    with pytest.raises(RuntimeError,match='AUDIT FAILURE'):
        restore_month_backup(settings,path,confirmed_month='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]=='KEEP ON FAILURE'


def test_restore_after_reset_recovers_shared_diploma_and_stage_links(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.application_reset import reset_application,ResetOptions,reset_target
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(1,'MILITAIRE','AUGUST DIPLOMA','2026-08-15')")
            cur.execute("INSERT INTO ref_type_stage(code,libelle) VALUES('TEST','TEST STAGE') ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)")
            cur.execute("INSERT INTO parcours_stage(id,agent_id,sme_periode_id,type_stage_code,date_debut,date_fin) VALUES(88,1,1,'TEST','2026-09-01','2026-09-15')")
            cur.execute("INSERT INTO agent_position_event(agent_id,position_admin,date_debut,mois_sme,reference_decision) VALUES(1,'Stage interne','2026-09-01','2026-09','sgi:auto:parcours_stage:88')")
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    assert inspect_month_backup(path)['shared_diplomas']
    monkeypatch.setattr('sme_gend.services.application_reset_files.reset_local_paths',lambda *a,**kw:[])
    reset_application(settings,ResetOptions(),confirmation=reset_target(settings))
    restore_month_backup(settings,path,confirmed_month='2026-09')
    rows=MySQLAgentRepository(settings).fetch_all()
    assert len(rows)==1 and rows[0].diplome=='AUGUST DIPLOMA'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id FROM parcours_stage')
            new_id=cur.fetchone()[0]
            cur.execute('SELECT reference_decision FROM agent_position_event')
            assert cur.fetchone()[0]=='sgi:auto:parcours_stage:'+str(new_id)
            cur.execute("SELECT annee_mois FROM sme_periode")
            assert cur.fetchall()==(('2026-09',),)


def test_restore_protects_other_month_display_from_shared_diploma_change(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(1,'MILITAIRE','OLD AUG','2026-08-15')")
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_diplome WHERE libelle_libre='OLD AUG'")
            cur.execute("UPDATE carriere_situation SET grade_libelle='KEEP ON CONFLICT' WHERE sme_periode_id=1")
    with pytest.raises(ValueError,match='autre mois'):
        restore_month_backup(settings,path,confirmed_month='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM agent_diplome WHERE libelle_libre='OLD AUG'")
            assert cur.fetchone()[0]==0
            cur.execute('SELECT grade_libelle FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]=='KEEP ON CONFLICT'


def test_restore_remaps_automatic_link_to_stage_in_another_month(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO ref_type_stage(code,libelle) VALUES('TEST','TEST STAGE') ON DUPLICATE KEY UPDATE libelle=VALUES(libelle)")
            cur.execute("INSERT INTO parcours_stage(id,agent_id,sme_periode_id,type_stage_code,date_debut,date_fin) VALUES(88,1,2,'TEST','2026-08-01','2026-08-31')")
            cur.execute("INSERT INTO agent_position_event(agent_id,position_admin,date_debut,mois_sme,reference_decision) VALUES(1,'Present','2026-09-01','2026-09','sgi:auto:parcours_stage:88')")
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    assert inspect_month_backup(path)['auto_sources']
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('UPDATE parcours_stage SET id=777 WHERE id=88')
    before=snapshot(connect,params)
    restore_month_backup(settings,path,confirmed_month='2026-09')
    assert snapshot(connect,params)==before
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT reference_decision FROM agent_position_event')
            assert cur.fetchone()[0]=='sgi:auto:parcours_stage:777'
            cur.execute('SELECT id,sme_periode_id FROM parcours_stage')
            assert cur.fetchall()==((777,2),)


def test_restore_refuses_ambiguous_identity_without_changing_month(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=setup(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE agent SET matricule_militaire='MIL-TEST' WHERE id=1")
    path=tmp_path/'month.zip';create_month_backup(settings,'2026-09',path)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET matricule_militaire=NULL WHERE id=1")
            cur.execute("INSERT INTO agent(id,legacy_access_id,noms,matricule_militaire) VALUES(99,'OTHER','OTHER PERSON','MIL-TEST')")
            cur.execute("UPDATE carriere_situation SET grade_libelle='KEEP ON CONFLICT' WHERE sme_periode_id=1")
    with pytest.raises(ValueError,match='ambigu'):
        restore_month_backup(settings,path,confirmed_month='2026-09')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle FROM carriere_situation WHERE sme_periode_id=1')
            assert cur.fetchone()[0]=='KEEP ON CONFLICT'
