"""Actor and action timestamps must survive every audit entry path."""
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from sme_gend.services import audit_log as audit,auth_context

@pytest.fixture
def recorded(monkeypatch):
    calls=[]
    class Repo:
        def __init__(self,*a):pass
        def table_exists(self,*a):return True
        def append_audit(self,**kw):calls.append(kw)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant',lambda:'2026-09')
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',Repo)
    monkeypatch.setattr(auth_context,'resolve_audit_actor',lambda:'SYN OPERATOR')
    return calls

def test_personnel_supplied_action_timestamp_reaches_mysql(recorded):
    audit.append_personnel_audit(action='update',personnel_id='42',changes={},ts_utc='2020-02-01T00:30:00+01:00')
    assert recorded[0]['ts_utc']==datetime(2020,1,31,23,30,tzinfo=timezone.utc)

def test_personnel_invalid_action_timestamp_is_refused_before_mysql(recorded):
    with pytest.raises(ValueError):audit.append_personnel_audit(action='update',personnel_id='42',changes={},ts_utc='NOT A DATE')
    assert not recorded

def test_missing_audit_table_never_silently_succeeds(monkeypatch,recorded):
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository.table_exists',lambda *a:False)
    with pytest.raises(RuntimeError):audit.append_audit_event(action='SYN CREATE')

def test_connected_audit_actor_uses_unique_login_not_display_name(monkeypatch):
    monkeypatch.setattr(auth_context,'_app_auth',SimpleNamespace(current_session=lambda:SimpleNamespace(username='operator_a',display_name='SAME DISPLAY NAME')))
    assert auth_context.resolve_audit_actor()=='operator_a'

def test_personnel_edited_by_does_not_replace_authenticated_operator(monkeypatch):
    monkeypatch.setattr(auth_context,'_app_auth',SimpleNamespace(current_session=lambda:SimpleNamespace(username='operator_a',display_name='SAME DISPLAY NAME')))
    calls=[];monkeypatch.setattr(audit,'_append_audit_mysql',lambda **kw:calls.append(kw))
    audit.append_personnel_audit(action='create',personnel_id='42',changes={},edited_by='FORM AUTHOR')
    assert calls[0]['actor']=='operator_a'

@pytest.mark.parametrize('progress',[False,True])
def test_background_action_keeps_the_user_who_started_it(monkeypatch,progress):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.bg_task import FunctionWorker,_ProgressWorker
    app=QApplication.instance() or QApplication([])
    current=[SimpleNamespace(username='operator_a',display_name='SAME DISPLAY NAME')]
    monkeypatch.setattr(auth_context,'_app_auth',SimpleNamespace(current_session=lambda:current[0]))
    run=lambda *args:auth_context.resolve_audit_actor()
    worker=_ProgressWorker(run,1) if progress else FunctionWorker(run)
    current[0]=SimpleNamespace(username='operator_b',display_name='SAME DISPLAY NAME')
    seen=[];worker.succeeded.connect(seen.append);worker.run()
    assert seen==['operator_a']
    assert auth_context.resolve_audit_actor()=='operator_b'



def test_activity_extra_cannot_replace_the_authoritative_timestamp(monkeypatch):
    calls=[];monkeypatch.setattr(audit,'_append_audit_mysql',lambda **kw:calls.append(kw))
    monkeypatch.setattr(audit,'_json_mirror_enabled',lambda:True)
    monkeypatch.setattr(audit,'_user_activity_path',lambda:__import__('pathlib').Path('synthetic.jsonl'))
    audit.append_user_activity(action='SYN',actor='SYN OPERATOR',ts_utc='FORGED')
    row=calls[0]['mirror'][1][0]
    assert row['ts_utc']!='FORGED' and datetime.fromisoformat(row['ts_utc'])==calls[0]['ts_utc']


def test_retroactive_mirror_uses_the_utc_month_and_same_timestamp_as_mysql(monkeypatch,tmp_path):
    calls=[];monkeypatch.setattr(audit,'_append_audit_mysql',lambda **kw:calls.append(kw))
    monkeypatch.setattr(audit,'_json_mirror_enabled',lambda:True)
    monkeypatch.setattr(audit,'_audit_dir',lambda:tmp_path)
    audit.append_personnel_audit(action='update',personnel_id='42',changes={},edited_by='SYN',ts_utc='2020-02-01T00:30:00+01:00')
    path,entries=calls[0]['mirror']
    assert path.name=='personnel_audit_2020-01.jsonl'
    assert datetime.fromisoformat(entries[0]['ts_utc'])==calls[0]['ts_utc']


def test_auth_activity_uses_the_initiating_user_after_a_session_switch(monkeypatch):
    from sme_gend.services.auth_service import AuthService
    from sme_gend.services.audit_log import append_user_activity
    auth=AuthService.__new__(AuthService);auth._session=SimpleNamespace(username='operator_b')
    calls=[];monkeypatch.setattr('sme_gend.services.audit_log.append_user_activity',lambda **kw:calls.append(kw))
    with auth_context.audit_identity_scope('operator_a'):
        auth._audit(action='user_created',detail='TARGET')
    assert calls[0]['actor']=='operator_a'



def test_personnel_timeline_never_reads_an_old_local_profile_by_default(monkeypatch):
    from sme_gend.services import personnel_dossier_history as history
    monkeypatch.setattr(history,'_iter_audit_updates_mysql',lambda pid,**kw:iter([]))
    monkeypatch.setattr(history,'_all_audit_files',lambda:pytest.fail('old local profile must not enter the central timeline'))
    assert history.build_audit_timeline('42')==[]


def test_personnel_timeline_mysql_failure_is_explicit_and_sanitized(monkeypatch):
    from sme_gend.services import personnel_dossier_history as history
    class Repo:
        def __init__(self,*a):pass
        def table_exists(self,*a):return True
        def read_personnel_audit_for(self,pid):raise RuntimeError('PRIVATE DRIVER DETAIL')
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',Repo)
    with pytest.raises(RuntimeError,match='Historique central') as error:
        history.build_audit_timeline('42',settings=object())
    assert 'PRIVATE' not in str(error.value)

@pytest.mark.parametrize('error',[False,True])
def test_personnel_history_tab_uses_internal_identity_source_and_shows_actor(monkeypatch,error):
    from PySide6.QtWidgets import QApplication,QTableWidget
    from sme_gend.ui import personnel_dossier_dialog as ui
    app=QApplication.instance() or QApplication([])
    seen=[];settings=object();table=QTableWidget(0,6)
    def load(pid,**kw):
        seen.append((pid,kw['settings']))
        if error:raise RuntimeError('central unavailable')
        return [SimpleNamespace(ts_utc='2026-09-01T00:00:00+00:00',sme_mois_context='2026-09',edited_by='operator_a',
            career_changes={},profile_changes={'noms':{'old':'A','new':'B'}})]
    monkeypatch.setattr(ui,'build_audit_timeline',load)
    view=SimpleNamespace(_row=SimpleNamespace(satellite_join_key=lambda:None,id='7'),_settings=settings,_profil_table=table)
    ui.PersonnelDossierDialog._refresh_profil_tab(view)
    assert seen==[('7',settings)]
    if error:assert 'indisponible' in table.item(0,0).text() and table.columnSpan(0,0)==6
    else:assert table.item(0,5).text()=='operator_a'
    table.close();table.deleteLater();app.processEvents()


def test_local_history_sort_uses_actual_utc_dates_not_iso_string_order(monkeypatch,tmp_path):
    import json
    from sme_gend.services import personnel_dossier_history as history
    path=tmp_path/'synthetic.jsonl'
    rows=[dict(action='update',personnel_id='42',ts_utc='2020-02-01T00:30:00+01:00',edited_by='EARLIER'),
          dict(action='update',personnel_id='42',ts_utc='2020-01-31T23:45:00Z',edited_by='LATER')]
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
    monkeypatch.setattr(history,'_all_audit_files',lambda:[path])
    assert [entry.edited_by for entry in history.build_audit_timeline('42',source='local')]==['EARLIER','LATER']


def test_deleted_history_sort_handles_invalid_and_explicit_utc_dates(monkeypatch):
    from sme_gend.services import personnel_deleted_service as service
    rows=[dict(id='1',ts='INVALID'),dict(id='2',ts='2026-09-01T00:00:00+00:00')]
    monkeypatch.setattr(service,'AppConfigRepository',lambda settings:SimpleNamespace(list_personnel_delete_audit=lambda **kw:rows))
    monkeypatch.setattr(service,'ListeGeneraleRepository',lambda settings:object())
    monkeypatch.setattr(service,'_entry_from_audit',lambda raw:SimpleNamespace(deleted_career_id=raw['id'],personnel_id=raw['id'],ts_utc=raw['ts'],row=None))
    assert [entry.deleted_career_id for entry in service.list_deleted_personnel(object())]==['2','1']
