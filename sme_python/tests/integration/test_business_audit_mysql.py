"""CRUD audit tests on synthetic disposable native MySQL schemas only."""
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from test_restore_mysql import isolated_mysql,_lot7_dossier
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services import audit_log as audit

@pytest.fixture
def journal(isolated_mysql,monkeypatch):
    from sme_gend.repositories.mysql_common import _split_sql_statements
    settings,connect,params=isolated_mysql
    sql=(Path(__file__).resolve().parents[2]/'data/schema/mysql_app_config.sql').read_text(encoding='utf-8')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):cur.execute(stmt)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant',lambda:'2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    monkeypatch.setenv('SME_AUDIT_JSON_MIRROR','0')
    monkeypatch.setenv('SME_SENSITIVE_AUDIT_JSON_MIRROR','0')
    return settings,connect,params

def traces(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT action,actor,ts_utc,module,personnel_id,sme_mois_context,payload FROM audit_journal ORDER BY id')
            return cur.fetchall()

def test_personnel_retroactive_action_keeps_utc_and_is_not_the_insertion_date(journal):
    audit.append_personnel_audit(action='update',personnel_id='42',changes={},ts_utc='2020-02-01T00:30:00+01:00')
    row=traces(journal[1],journal[2])[0]
    assert row[2]==datetime(2020,1,31,23,30) and row[1]=='SYN OPERATOR'

@pytest.mark.parametrize('family',['mission','stage','malade','desertion','conge'])
def test_dated_dossier_create_update_delete_each_has_a_trace(isolated_mysql,monkeypatch,family):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings,connect,params,core,row=_lot7_dossier(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    monkeypatch.setenv('SME_SENSITIVE_AUDIT_JSON_MIRROR','0')
    values={
        'mission':dict(date_debut='01/09/2026',date_fin=None,nature='SYN INITIAL',lieu=None,reference_dossier=None,observation=None),
        'stage':dict(type_code='LOCAL',date_debut='01/09/2026',date_fin=None,nature='SYN INITIAL',lieu=None,observation=None),
        'malade':dict(date_debut=date(2026,9,1),date_fin=None,pathologie='SYN INITIAL',observation=None),
        'desertion':dict(date_desertion='01/09/2026',observation=None),
        'conge':dict(type_code='ADMIN',date_debut=date(2026,9,1),date_fin=None,quota_jours=None,observation=None,sme_mois='2026-09')}
    reader={'mission':'missions','stage':'stages','malade':'malades','desertion':'desertions','conge':'conges'}[family]
    svc=AgentDossierEventsService(settings);getattr(svc,'add_'+family)('42',**values[family])
    shown=getattr(svc,reader)('42')[0];identifier=getattr(shown,'malade_id' if family=='malade' else 'id')
    args=(identifier,) if family=='malade' else ('42',identifier)
    changed=values[family]|dict(observation='SYN EDIT')
    getattr(svc,'update_'+family)(*args,**changed,expected=shown)
    shown=getattr(svc,reader)('42')[0]
    getattr(svc,'delete_'+family)(*args,expected=shown)
    found=traces(connect,params)
    assert [r[0] for r in found]==['dossier_'+family+'_'+a for a in ('add','update','delete')]
    assert all(r[1]=='SYN OPERATOR' and r[2] and r[3]=='sensitive_data' for r in found)

@pytest.mark.parametrize('kind',['grade','structure','fonction'])
def test_reference_crud_has_distinct_trace_for_each_confirmed_action(isolated_mysql,monkeypatch,kind):
    settings,connect,params,core,row=_lot7_dossier(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    from sme_gend.repositories.mysql_bundle_repositories import MySQLGradeRepository,MySQLStructureRepository,MySQLFonctionsRepository
    if kind=='grade':
        repo=MySQLGradeRepository(settings);repo.insert('SYN','INITIAL',1,1)
        old=next(r for r in repo.fetch_all() if r.code=='SYN');repo.update('SYN','CHANGED',1,1,expected=old)
        old=next(r for r in repo.fetch_all() if r.code=='SYN');repo.delete('SYN',expected=old)
    elif kind=='structure':
        repo=MySQLStructureRepository(settings);repo.insert('SYN','INITIAL',1)
        old=next(r for r in repo.fetch_all() if r.code=='SYN');repo.update('SYN','CHANGED',1,expected=old)
        old=next(r for r in repo.fetch_all() if r.code=='SYN');repo.delete('SYN',expected=old)
    else:
        repo=MySQLFonctionsRepository(settings);identifier=repo.insert('INITIAL')
        old=next(r for r in repo.fetch_all() if r.id==identifier);repo.update(identifier,'INITIAL','CHANGED',expected=old)
        old=next(r for r in repo.fetch_all() if r.id==identifier);repo.delete(identifier,expected=old)
    found=traces(connect,params)
    assert [r[0] for r in found]==[kind+'_'+a for a in ('create','update','delete')]
    assert all(r[1]=='SYN OPERATOR' for r in found)

@pytest.mark.parametrize('operation',['add','update','delete'])
def test_auth_config_and_audit_are_atomic(journal,monkeypatch,operation):
    from sme_gend.services.auth_service import AuthService
    auth=AuthService();auth.ensure_default_admin('Synthetic_Password_123')
    auth.add_user('subject','Synthetic_Password_456')
    before=AppConfigRepository(journal[0]).get_json('auth_config')
    def fail(**kw):raise RuntimeError('synthetic audit failure')
    monkeypatch.setattr(auth,'_audit',fail)
    with pytest.raises(RuntimeError):
        if operation=='add':auth.add_user('new_subject','Synthetic_Password_789')
        elif operation=='update':auth.update_user('subject',display_name='CHANGED')
        else:auth.remove_user('subject')
    assert AppConfigRepository(journal[0]).get_json('auth_config')==before



def test_personnel_audit_payload_cannot_replace_authoritative_metadata(journal):
    repository=AppConfigRepository(journal[0])
    repository.append_audit(action='delete',module='personnel',actor='REAL OPERATOR',personnel_id='42',
        ts_utc=datetime(2026,9,1,tzinfo=timezone.utc),payload={'ts_utc':'FORGED','edited_by':'FORGED','personnel_id':'999','action':'FORGED'})
    for result in (repository.read_personnel_audit_for('42',action='delete'),repository.list_personnel_delete_audit()):
        assert len(result)==1
        assert (result[0]['action'],result[0]['edited_by'],result[0]['personnel_id'])==('delete','REAL OPERATOR','42')
        assert result[0]['ts_utc']=='2026-09-01T00:00:00+00:00'


def test_caught_auth_audit_failure_rolls_back_to_outer_savepoint(journal,monkeypatch):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    from sme_gend.services.auth_service import AuthService
    auth=AuthService();auth.ensure_default_admin('Synthetic_Password_123')
    before=AppConfigRepository(journal[0]).get_json('auth_config')
    monkeypatch.setattr(auth,'_audit',lambda **kw:(_ for _ in ()).throw(RuntimeError('synthetic audit failure')))
    with mysql_import_transaction(journal[0]):
        with pytest.raises(RuntimeError):auth.add_user('rolled_back','Synthetic_Password_456')
        AppConfigRepository(journal[0]).set_json('outer_survives',{'ok':True})
    same=AppConfigRepository(journal[0]).get_json('auth_config')==before
    assert same
    assert AppConfigRepository(journal[0]).get_json('outer_survives')=={'ok':True}

@pytest.mark.parametrize('operation',['gain_insert','gain_update','gain_delete','task_create','task_resolve'])
def test_movements_and_workflow_have_one_trace_after_each_success(isolated_mysql,monkeypatch,operation):
    from test_restore_mysql import _business_lot20_setup
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    monkeypatch.setattr('sme_gend.services.workflow_task_service.resolve_audit_actor',lambda:'SYN OPERATOR')
    actions[operation]()
    found=traces(connect,params)
    assert len(found)==1 and found[0][1]=='SYN OPERATOR' and found[0][2]
    assert found[0][3]==('workflow' if operation.startswith('task') else 'gain_perte')
    assert not any('write_token' in str(r[6]) for r in found)

@pytest.mark.parametrize('operation',['gain_insert','task_create'])
def test_failed_business_audit_leaves_neither_data_nor_revision(isolated_mysql,monkeypatch,operation):
    from test_restore_mysql import _business_lot20_setup,_business_lot20_snapshot
    from sme_gend.services.data_sync import read_data_revision
    settings,connect,params,repo,tasks,report,row,actions=_business_lot20_setup(isolated_mysql,monkeypatch)
    before=_business_lot20_snapshot(connect,params);revision=read_data_revision(settings)
    monkeypatch.setattr('sme_gend.services.business_audit.record_business_action',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('synthetic audit failure')))
    with pytest.raises(RuntimeError):actions[operation]()
    assert _business_lot20_snapshot(connect,params)==before and read_data_revision(settings)==revision
    assert not traces(connect,params)


def test_new_personnel_create_update_delete_traces_match_the_actual_actions(isolated_mysql,monkeypatch):
    from dataclasses import replace
    from sme_gend.services.personnel_service import PersonnelService
    settings,connect,params,core,row=_lot7_dossier(isolated_mysql,monkeypatch)
    settings=replace(settings,mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant',lambda:'2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYN OPERATOR')
    monkeypatch.setenv('SME_SENSITIVE_AUDIT_JSON_MIRROR','0')
    svc=PersonnelService(settings)
    identifier=svc.create_personnel(replace(row,id='0',access_join_id=None,noms='SYN NEW',grade=None))
    current=svc.get_by_id(identifier);svc.update_personnel(current,replace(current,noms='SYN CHANGED'))
    current=svc.get_by_id(identifier);svc.delete_personnel(current,motif='SORTIE SYNTHETIQUE')
    found=[r for r in traces(connect,params) if r[3]=='personnel']
    assert [r[0] for r in found]==['create','update','delete']
    assert all(r[1]=='SYN OPERATOR' and r[5]=='2026-09' for r in found)
    assert all(json.loads(r[6]).get('before') is not None for r in found)



def test_audit_precision_cannot_round_an_action_into_the_next_utc_day(journal):
    stamp=datetime(2020,1,31,23,59,59,999900,tzinfo=timezone.utc)
    AppConfigRepository(journal[0]).append_audit(action='SYN',module='personnel',ts_utc=stamp)
    assert traces(journal[1],journal[2])[0][2]==datetime(2020,1,31,23,59,59,999000)



def test_missing_personnel_timeline_table_is_an_error_not_an_empty_history(journal):
    from sme_gend.services.personnel_dossier_history import build_audit_timeline
    with journal[1](**journal[2]) as conn:
        with conn.cursor() as cur:cur.execute('DROP TABLE audit_journal')
    with pytest.raises(RuntimeError,match='Historique central'):
        build_audit_timeline('42',settings=journal[0])



def test_failed_login_audit_never_leaves_an_authenticated_session(journal,monkeypatch):
    from sme_gend.services.auth_service import AuthService
    auth=AuthService();auth.ensure_default_admin('Synthetic_Password_123');auth.set_enabled(True)
    released=[]
    monkeypatch.setattr('sme_gend.services.auth_session_registry.try_register_session',lambda *a,**kw:('synthetic-session-token',None))
    monkeypatch.setattr('sme_gend.services.auth_session_registry.unregister_session',lambda settings,token:released.append(token))
    monkeypatch.setattr(auth,'_audit',lambda **kw:(_ for _ in ()).throw(RuntimeError('synthetic audit failure')))
    with pytest.raises((RuntimeError,ValueError)):
        auth.authenticate('admin','Synthetic_Password_123')
    assert auth.current_session() is None and released==['synthetic-session-token']
