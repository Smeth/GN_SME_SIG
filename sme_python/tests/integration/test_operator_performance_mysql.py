from datetime import date,datetime,timedelta,timezone
from dataclasses import replace
from types import SimpleNamespace
import json,time
import pytest
from PySide6.QtWidgets import QApplication
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services
from sme_gend.services import sensitive_data_policy as policy
ORIGINAL_HISTORY_GUARD=policy.assert_may_mutate_dossier_history
ORIGINAL_FIELD_GUARD=policy.assert_may_edit_personnel_sensitive_fields


def install_role(monkeypatch,editable,role_id="operateur"):
    from sme_gend.config.auth_config import AuthConfig,AuthRole
    from sme_gend.services.auth_service import AuthService,AuthSession
    from sme_gend.services import auth_context
    auth=AuthService.__new__(AuthService)
    permissions=['effectifs.liste','situation.stages','effectifs.export']
    if editable:permissions+=['effectifs.edition','effectifs.sme_dpf']
    auth._config=AuthConfig(enabled=True,roles=[AuthRole(role_id,'Operateur',permissions=permissions)])
    auth._enforced=True;auth._session_verification_pending=False
    auth._session=AuthSession('OP','OPERATEUR',role_id,datetime.now(timezone.utc)+timedelta(hours=1))
    monkeypatch.setattr(auth_context,'_app_auth',auth)
    monkeypatch.setattr(policy,'assert_may_mutate_dossier_history',ORIGINAL_HISTORY_GUARD)
    monkeypatch.setattr(policy,'assert_may_edit_personnel_sensitive_fields',ORIGINAL_FIELD_GUARD)
    assert not policy.session_may_edit_sensitive_data()
    return auth


@pytest.mark.parametrize('kind',['mission','stage','malade','conge','desertion','position'])
def test_real_operator_can_save_monthly_position_and_its_dated_record(isolated_mysql,monkeypatch,kind):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_COL_IDX
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.position_admin_events import add_position_event
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    install_role(monkeypatch,True)
    events=AgentDossierEventsService(settings)
    if kind=='mission':
        label='Mission';table='agent_mission';callback=lambda:events.add_mission('42',date_debut=date(2026,9,1),date_fin=None,nature='SYN',lieu=None,reference_dossier=None,observation=None)
    elif kind=='stage':
        label='StageLocal';table='parcours_stage';callback=lambda:events.add_stage('42',type_code='LOCAL',date_debut=date(2026,9,1),date_fin=None,nature='SYN',lieu=None,observation=None)
    elif kind=='malade':
        label='Malade';table='agent_malade';callback=lambda:events.add_malade('42',date_debut=date(2026,9,1),date_fin=None,pathologie='SYN',observation=None)
    elif kind=='conge':
        label='Cong\u00e9Admin';table='agent_conge';callback=lambda:events.add_conge('42',type_code='ADMIN',date_debut=date(2026,9,1),date_fin=None,quota_jours=None,observation=None,sme_mois='2026-09')
    elif kind=='desertion':
        label='D\u00e9serteur';table='agent_desertion';callback=lambda:events.add_desertion('42',date_desertion=date(2026,9,1),observation=None)
    else:
        label='D\u00e9tach\u00e9';table='agent_position_event';callback=lambda:add_position_event(settings,'42',position_admin=label,date_debut=date(2026,9,1),date_fin=None,lieu='SYN',reference_decision='OG 42',observation=None,mois_sme='2026-09')
    app=QApplication.instance() or QApplication([])
    model=_EditionModel();model.load([personnel.get_by_id('1')],None,[],[],[],[],personnel=personnel,settings=settings)
    model.position_change_prompt=lambda *args:callback
    errors=[];model.save_err.connect(errors.append)
    assert model.setData(model.index(0,_COL_IDX['position_admin']),label),errors
    assert personnel.get_by_id('1').position_admin==label
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM {table} WHERE agent_id=1')
            assert cur.fetchone()[0]>=1
    # Editors can add operational data, but cannot rewrite existing history.
    with pytest.raises(policy.SensitiveDataPermissionError):
        policy.assert_may_mutate_dossier_history(action='modifier un ancien historique')
    app.processEvents()


def test_operator_can_record_diploma_and_closed_month_grade_correction(isolated_mysql,monkeypatch):
    from test_closed_month_corrections_mysql import closed_calendar,snapshots
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    install_role(monkeypatch,True)
    row=personnel.get_by_id('6')
    personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,7,20),ndecision='OG OP'))
    assert all(g=='ADM' for g in snapshots(connect,params,'c.grade_libelle').values())
    dossier.add_diplome(personnel.get_by_id('6'),categorie='MILITAIRE',libelle='OP DIPLOMA',date_obtention=date(2026,7,25))
    assert all(d=='OP DIPLOMA' for d in snapshots(connect,params,'c.diplome_libelle').values())


def test_read_only_account_is_rejected_before_any_write(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.position_admin_events import add_position_event
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    install_role(monkeypatch,False)
    events=AgentDossierEventsService(settings)
    actions=[lambda:personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,9,1))),
             lambda:dossier.add_diplome(row,categorie='MILITAIRE',libelle='DENIED',date_obtention=date(2026,9,1)),
             lambda:events.add_desertion('42',date_desertion=date(2026,9,1),observation=None),
             lambda:add_position_event(settings,'42',position_admin='Punis',date_debut=date(2026,9,1),date_fin=None,lieu=None,reference_decision=None,observation=None,mois_sme='2026-09')]
    for action in actions:
        with pytest.raises(policy.SensitiveDataPermissionError):action()
    assert personnel.get_by_id('1').grade==row.grade
    assert not dossier.list_diplomes(row) and not events.desertions('42')


def test_loading_twenty_thousand_rows_preserves_latest_diploma(isolated_mysql,monkeypatch,tmp_path):
    from pathlib import Path
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        conn.begin()
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO agent(id,legacy_access_id,noms) VALUES(%s,%s,%s)',[(i,f'SYN-{i}',f'SYN AGENT {i}')for i in range(3,20003)])
            cur.executemany("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle) VALUES(%s,%s,1,'MDL')",[(i,i)for i in range(3,20003)])
            cur.executemany("INSERT INTO agent_diplome(agent_id,categorie,libelle_libre,date_obtention) VALUES(%s,'MILITAIRE',%s,%s)",[(i,label,stamp)for i in range(3,20003)for label,stamp in [('LATEST','2026-09-01'),('OLDER INSERTED LAST','2026-07-01')]])
        conn.commit()
    import sme_gend.repositories.mysql_agent_repository as agent_repo
    original_select=agent_repo._select_liste
    def bounded_select(*args,**kwargs):
        sql,bindings=original_select(*args,**kwargs)
        return sql.replace('SELECT','SELECT /*+ MAX_EXECUTION_TIME(10000) */',1),bindings
    monkeypatch.setattr(agent_repo,'_select_liste',bounded_select)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            sql,bindings=bounded_select(None)
            cur.execute('EXPLAIN '+sql,bindings)
            plan=cur.fetchall()
            (tmp_path/'benchmark-mysql-plan-apres-20261105.json').write_text(json.dumps(plan,indent=2,default=str),encoding='utf-8')
    start=time.perf_counter();rows=personnel._repo.fetch_all();elapsed=time.perf_counter()-start
    assert len(rows)==20001
    assert all(r.diplome=='LATEST' for r in rows if r.id!='1')
    report={'rows':len(rows),'diplomas':40000,'local_mysql_fetch_seconds':round(elapsed,6),'production_data_touched':False}
    (tmp_path/'benchmark-mysql-chargement-20261105.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(report)


def test_real_operator_can_finish_stage_and_save_diploma(isolated_mysql,monkeypatch):
    import test_sme_stage_persistence_mysql as stages
    original_setup=stages.setup_services
    def operator_setup(*args,**kwargs):
        result=original_setup(*args,**kwargs)
        install_role(monkeypatch,True)
        return result
    monkeypatch.setattr(stages,'setup_services',operator_setup)
    stages.test_stage_end_and_diploma_commit_together(isolated_mysql,monkeypatch,'local',False,False)


def test_existing_database_missing_search_index_is_upgraded_without_data_loss(isolated_mysql,monkeypatch):
    from sme_gend.services.mysql_import_period import ensure_mysql_migrations
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    dossier.add_diplome(personnel.get_by_id('1'),categorie='MILITAIRE',libelle='KEEP EXISTING',date_obtention=date(2026,9,1))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('ALTER TABLE agent_diplome ADD KEY legacy_agent (agent_id)')
            cur.execute('ALTER TABLE agent_diplome DROP INDEX idx_agent_diplome_agent')
    ensure_mysql_migrations(settings)
    assert personnel.get_by_id('1').diplome=='KEEP EXISTING'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='agent_diplome' AND index_name='idx_agent_diplome_agent'")
            assert cur.fetchone()[0]==2
            cur.execute('SELECT COUNT(*) FROM agent_diplome')
            assert cur.fetchone()[0]==1


def test_custom_operator_role_can_make_retroactive_corrections(isolated_mysql,monkeypatch):
    original=install_role
    monkeypatch.setattr(__import__(__name__),'install_role',lambda patch,editable:original(patch,editable,role_id='custom'))
    test_operator_can_record_diploma_and_closed_month_grade_correction(isolated_mysql,monkeypatch)
