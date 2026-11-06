"""Read-only journal regression tests with isolated synthetic local mirrors."""
from datetime import date
import json
import pytest
from sme_gend.services import audit_log,doc_gen_log
from sme_gend.repositories.app_config_repository import AppConfigRepository

@pytest.fixture
def sources(monkeypatch,tmp_path):
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_auth_journal',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    monkeypatch.setattr(AppConfigRepository,'search_audit_journal',lambda *a,**kw:[])
    monkeypatch.setattr(AppConfigRepository,'read_doc_gen_tail',lambda *a,**kw:[])
    monkeypatch.setattr(AppConfigRepository,'table_exists',lambda *a:True)
    activity=tmp_path/'activity.jsonl';documents=tmp_path/'documents.jsonl'
    activity.write_text(json.dumps(dict(module='auth',action='OLD',actor='OLD BASE'))+'\n',encoding='utf-8')
    documents.write_text(json.dumps(dict(doc_type='attestation',persons=['OLD BASE']))+'\n',encoding='utf-8')
    monkeypatch.setattr(audit_log,'_user_activity_path',lambda:activity)
    monkeypatch.setattr(doc_gen_log,'_log_path',lambda:documents)
    return activity,documents

@pytest.mark.parametrize('kind',['auth','documents'])
def test_empty_central_journal_does_not_show_unfiltered_old_local_rows(sources,kind):
    assert (audit_log.search_auth_activity(actor='NO MATCH') if kind=='auth' else doc_gen_log.read_doc_log())==[]

@pytest.mark.parametrize('kind',['auth','documents'])
def test_read_error_is_visible_without_showing_driver_secret(sources,monkeypatch,kind):
    def fail(*a,**kw):raise OSError('SYNTHETIC PRIVATE DRIVER PARAMETER')
    name='search_audit_journal' if kind=='auth' else 'read_doc_gen_tail'
    monkeypatch.setattr(AppConfigRepository,name,fail)
    with pytest.raises(RuntimeError,match='journal central') as error:
        audit_log.search_auth_activity() if kind=='auth' else doc_gen_log.read_doc_log()
    assert 'PRIVATE DRIVER' not in str(error.value)

def test_auth_invalid_dates_do_not_return_local_success(sources):
    with pytest.raises(ValueError):audit_log.search_auth_activity(date_from=date(2026,10,2),date_to=date(2026,10,1))

def test_document_filter_ignores_accents_and_keeps_type_and_period():
    entry=dict(doc_type='attestation',mois='2026-09',persons=['ÉLOÏSE'])
    assert doc_gen_log.doc_entry_passes_filters(entry,type_key='attestation',mois_filter='2026-09',search_query='eloise')
    assert not doc_gen_log.doc_entry_passes_filters(entry,type_key='numerique',search_query='eloise')


@pytest.fixture(params=['auth','documents'])
def screen(request,monkeypatch):
    from PySide6.QtWidgets import QApplication,QWidget
    from sme_gend.ui.view_lifecycle import ViewDataState
    app=QApplication.instance() or QApplication([])
    from sme_gend.ui.views import users_auth_view as au,doc_gen_log_view as du
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    monkeypatch.setattr(au,'session_can_view_auth_journal',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    jobs=[]
    def launch(owner,work,done,**kw):jobs.append((work,done,kw['on_error']))
    kind=request.param
    if kind=='auth':
        monkeypatch.setattr(au,'run_in_background',launch)
        view=au.UsersAuthView.__new__(au.UsersAuthView);QWidget.__init__(view)
        view._test_tab=view._build_log_tab();view._view_data=ViewDataState();view._log_loaded=False
        view._apply_tab_permissions=lambda:None
        refresh=view._reload_activity_log_async;field=view._log_filter_actor;timer=view._log_timer
    else:
        monkeypatch.setattr(du,'run_in_background',launch)
        view=du.DocGenLogView();refresh=view.refresh;field=view._filter_search;timer=view._filter_timer
    yield kind,view,jobs,app,refresh,field,timer
    timer.stop();view.close();view.deleteLater();app.processEvents()

def current_entries(kind,view):return view._log_lines if kind=='auth' else view._entries
def result(kind,name):
    return [dict(actor=name,detail='SYNTHETIC')] if kind=='auth' else ([dict(doc_type='attestation',persons=[name],mois='2026-09')],['2026-09'])

def test_journal_filter_change_invalidates_old_result_and_coalesces_reads(screen):
    kind,view,jobs,app,refresh,field,timer=screen
    refresh();assert len(jobs)==1
    field.setText('NEW');refresh();assert len(jobs)==1
    jobs[0][1](result(kind,'OLD'));assert current_entries(kind,view)==[]
    app.processEvents();assert len(jobs)==2
    jobs[-1][1](result(kind,'NEW'))
    assert current_entries(kind,view) and 'NEW' in str(current_entries(kind,view))

def test_journal_error_clears_rows_and_export_cache_without_driver_secret(screen):
    kind,view,jobs,app,refresh,field,timer=screen
    refresh();jobs[0][1](result(kind,'FIRST'));assert current_entries(kind,view)
    refresh();jobs[-1][2](RuntimeError('SYNTHETIC DRIVER PASSWORD'))
    assert current_entries(kind,view)==[]
    label=view._lbl_log_count if kind=='auth' else view._lbl_count
    assert 'journal central' in label.text() and 'PASSWORD' not in label.text()

def test_journal_revoked_permission_never_applies_old_rows(screen,monkeypatch):
    kind,view,jobs,app,refresh,field,timer=screen
    refresh()
    if kind=='auth':monkeypatch.setattr('sme_gend.ui.views.users_auth_view.session_can_view_auth_journal',lambda:False)
    else:monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:False)
    jobs[0][1](result(kind,'PRIVATE'))
    assert current_entries(kind,view)==[]

def test_journal_runtime_invalidation_prevents_stale_base_result(screen):
    kind,view,jobs,app,refresh,field,timer=screen
    refresh()
    view.mark_view_stale()
    jobs[0][1](result(kind,'OLD DATABASE'))
    assert current_entries(kind,view)==[]

def test_document_filters_survive_page_activation_configuration(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_doc_log',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_attestation_history',lambda:True)
    view=DocGenLogView();view._filter_type.setCurrentIndex(view._filter_type.findData('attestation'))
    view._filter_timer.stop();view._configure_access_ui()
    assert view._filter_type.currentData()=='attestation'
    view.close();view.deleteLater();app.processEvents()

@pytest.mark.parametrize('kind',['auth','documents'])
def test_service_permission_is_checked_before_query(sources,monkeypatch,kind):
    calls=[]
    monkeypatch.setattr(AppConfigRepository,'search_audit_journal',lambda *a,**kw:calls.append('SQL'))
    monkeypatch.setattr(AppConfigRepository,'read_doc_gen_tail',lambda *a,**kw:calls.append('SQL'))
    permission='session_can_view_auth_journal' if kind=='auth' else 'session_can_view_attestation_history'
    monkeypatch.setattr('sme_gend.services.auth_guard.'+permission,lambda:False)
    with pytest.raises(PermissionError):
        audit_log.search_auth_activity() if kind=='auth' else doc_gen_log.read_doc_log()
    assert calls==[]
