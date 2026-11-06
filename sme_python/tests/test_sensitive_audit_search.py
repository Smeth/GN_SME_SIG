"""Synthetic audit regressions; never read operational logs."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
import pytest
from sme_gend.services import sensitive_audit_log as audit

@pytest.fixture
def source(monkeypatch):
    calls=[]
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:True)
    def result(self,**kw):calls.append(kw);return []
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository.search_audit_journal',result)
    monkeypatch.setattr(audit,'_parse_local_entries',lambda n:[dict(module='sensitive_data',action='LOCAL_OLD',actor='old database')])
    return calls

def test_empty_central_result_does_not_resurrect_local_history(source):
    assert audit.search_sensitive_audit(audit.SensitiveAuditQuery())==[]

def test_read_failure_is_not_reported_as_a_successful_local_result(source,monkeypatch):
    def fail(*a,**kw):raise OSError('synthetic secret must not be displayed')
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository.search_audit_journal',fail)
    with pytest.raises(RuntimeError,match='journal central') as error:
        audit.search_sensitive_audit(audit.SensitiveAuditQuery())
    assert 'synthetic secret' not in str(error.value)

def test_reversed_dates_fail_before_query(source):
    with pytest.raises(ValueError,match='date'):
        audit.search_sensitive_audit(audit.SensitiveAuditQuery(date_from=date(2026,10,2),date_to=date(2026,10,1)))
    assert source==[]

def test_permission_denied_before_query(source,monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:False)
    with pytest.raises(PermissionError):audit.search_sensitive_audit(audit.SensitiveAuditQuery())
    assert source==[]

def test_local_match_name_does_not_match_an_unrelated_detail():
    entry=dict(personnel_nom='ALPHA',detail='Contact BETA')
    assert not audit.entry_matches_query(entry,audit.SensitiveAuditQuery(personnel_nom='BETA'))

def test_local_match_personnel_number_is_exact():
    assert not audit.entry_matches_query(dict(personnel_id='112'),audit.SensitiveAuditQuery(personnel_id='12'))

def test_whitespace_action_is_normalized(source,monkeypatch):
    entry=dict(action='dossier_diplome_add')
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository.search_audit_journal',lambda *a,**kw:[entry])
    assert audit.search_sensitive_audit(audit.SensitiveAuditQuery(action=' dossier_diplome_add '))==[entry]

def test_qt_denial_clears_detail_cache(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:False)
    view=SensitiveAuditLogView();view._entries=[dict(detail='must clear')]
    view.refresh()
    assert view._entries==[]
    view.close()

def test_qt_view_can_be_invalidated_by_f5():
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView
    app=QApplication.instance() or QApplication([])
    view=SensitiveAuditLogView();view._view_data.finish_load()
    assert callable(getattr(view,'mark_view_stale',None))
    view.mark_view_stale();assert view._view_data.needs_load()
    view.close()

def test_qt_utc_naive_timestamp_is_displayed_in_local_time():
    from sme_gend.ui.views.sensitive_audit_log_view import _fmt_ts
    expected=datetime(2026,10,1,23,30,tzinfo=timezone.utc).astimezone().strftime('%d/%m/%Y %H:%M:%S')
    assert _fmt_ts('2026-10-01T23:30:00')==expected


@pytest.mark.parametrize('month',['2026-00','2026-13','0000','2026-01-extra'])
def test_invalid_month_is_rejected_before_sql(source,month):
    with pytest.raises(ValueError):audit.search_sensitive_audit(audit.SensitiveAuditQuery(sme_mois=month))
    assert source==[]

def test_local_date_match_uses_displayed_local_day():
    from sme_gend.repositories.app_config_repository import audit_date_bounds
    day=date(2026,10,2);start,end=audit_date_bounds(day,day)
    row=dict(ts_utc=start.isoformat())
    assert audit.entry_matches_query(row,audit.SensitiveAuditQuery(date_from=day,date_to=day))
    assert not audit.entry_matches_query(dict(ts_utc=end.isoformat()),audit.SensitiveAuditQuery(date_from=day,date_to=day))

def test_date_bounds_convert_each_local_midnight_for_dst(monkeypatch):
    from datetime import timedelta
    from sme_gend.repositories import app_config_repository as repo
    class LocalDateTime(datetime):
        @classmethod
        def combine(cls,day,clock):
            # A synthetic spring-forward day: UTC+1 at start, UTC+2 at next midnight.
            offset=1 if day==date(2026,3,29) else 2
            return datetime.combine(day,clock,tzinfo=timezone(timedelta(hours=offset)))
    monkeypatch.setattr(repo,'datetime',LocalDateTime)
    start,end=repo.audit_date_bounds(date(2026,3,29),date(2026,3,29))
    assert start==datetime(2026,3,28,23)
    assert end==datetime(2026,3,29,22)
    assert (end-start).total_seconds()==23*3600

@pytest.fixture
def screen(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views import sensitive_audit_log_view as ui
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:True)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:object())
    jobs=[]
    def launch(owner,work,done,**kw):jobs.append((work,done,kw['on_error']))
    monkeypatch.setattr(ui,'run_in_background',launch)
    view=ui.SensitiveAuditLogView()
    yield view,jobs,app,ui
    view._filter_timer.stop();view.close();view.deleteLater();app.processEvents()

def test_qt_changing_filter_schedules_search_and_clears_old_rows(screen):
    view,jobs,app,ui=screen
    view._apply_entries([dict(actor='OLD')],500)
    view._filter_actor.setText('NEW')
    assert view._entries==[] and view._filter_timer.isActive()
    assert jobs==[]
    view._filter_timer.timeout.emit()
    assert len(jobs)==1

def test_qt_sql_is_deferred_and_stale_result_does_not_repaint(screen,monkeypatch):
    view,jobs,app,ui=screen;seen=[]
    monkeypatch.setattr(ui,'search_sensitive_audit',lambda query,**kw:seen.append(query.actor) or [dict(actor=query.actor)])
    view.refresh();assert seen==[] and len(jobs)==1
    old_work,old_done,_=jobs[0]
    view._filter_actor.setText('NEW');view.refresh()
    assert len(jobs)==1 # one active read, one coalesced request
    old_done(old_work());assert view._entries==[]
    app.processEvents();assert len(jobs)==2
    work,done,_=jobs[-1];done(work())
    assert seen==['','NEW'] and view._entries==[dict(actor='NEW')]

def test_qt_runtime_database_change_rejects_previous_result(screen,monkeypatch):
    view,jobs,app,ui=screen;first=object();second=object();seen=[]
    monkeypatch.setattr(ui,'search_sensitive_audit',lambda query,**kw:seen.append(kw['settings']) or [dict(actor='FIRST')])
    view.apply_runtime_settings(first);view.refresh();work,done,_=jobs[0]
    view.apply_runtime_settings(second);done(work())
    assert seen==[first] and view._entries==[] and view._view_data.needs_load()
    view.refresh();jobs[-1][1](jobs[-1][0]())
    assert seen==[first,second]

def test_qt_error_is_visible_without_secret_or_old_details(screen):
    view,jobs,app,ui=screen
    view._apply_entries([dict(detail='OLD')],500);view.refresh()
    jobs[0][2](RuntimeError('password=DO_NOT_DISPLAY'))
    assert view._entries==[] and view._view_data.needs_load()
    assert 'journal central' in view._lbl_count.text()
    assert 'DO_NOT_DISPLAY' not in view._lbl_count.text()

def test_qt_revoked_permission_during_read_does_not_display_result(screen,monkeypatch):
    view,jobs,app,ui=screen;view.refresh()
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:False)
    jobs[0][1]([dict(detail='PRIVATE')])
    assert view._entries==[] and 'refusé' in view._lbl_count.text()

def test_qt_invalid_dates_are_visible_and_do_not_launch_sql(screen):
    from PySide6.QtCore import QDate
    view,jobs,app,ui=screen
    view._filter_date_from.setDate(QDate(2026,10,2));view._filter_date_to.setDate(QDate(2026,10,1))
    view.refresh()
    assert jobs==[] and 'date' in view._lbl_count.text()

def test_qt_year_month_and_reset_preserve_consistent_query(screen):
    view,jobs,app,ui=screen
    view._combo_year.setEditText('2001');view._combo_month.setCurrentIndex(2)
    assert view._mois_filter_value()=='2001-02'
    view._filter_actor.setText('ADMIN');view._filter_personnel_id.setText('GEN-1')
    view._reset_filters()
    query=view._build_query()
    assert query.sme_mois==query.actor==query.personnel_id==''
    assert query.date_from is None and query.date_to is None and query.limit==500
    assert len(jobs)==1

def test_qt_complete_payload_and_limit_are_visible(screen,monkeypatch):
    from PySide6.QtWidgets import QLabel,QPlainTextEdit
    from PySide6.QtCore import QModelIndex
    view,jobs,app,ui=screen;seen={}
    payload=dict(ndecision='TEST-42',date_debut='2026-09-01')
    view._apply_entries([dict(actor='<b>UNTRUSTED</b>',detail='<img src="remote">',payload=payload)],1)
    assert 'TEST-42' in view._model._rows[0][-1]
    assert 'limite' in view._lbl_count.text()
    def inspect(dialog):
        seen['labels']=[label.text() for label in dialog.findChildren(QLabel)]
        seen['payload']=dialog.findChild(QPlainTextEdit).toPlainText()
        return 0
    monkeypatch.setattr(ui.QDialog,'exec',inspect)
    view._on_row_double_clicked(view._model.index(0,0))
    assert 'TEST-42' in seen['payload']
    header=seen['labels'][0]
    assert '&lt;b&gt;UNTRUSTED&lt;/b&gt;' in header and '<img src=' not in header

def test_qt_detail_denied_after_permission_revocation(screen,monkeypatch):
    view,jobs,app,ui=screen;view._apply_entries([dict(detail='PRIVATE')],500)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit',lambda:False)
    monkeypatch.setattr(ui.QDialog,'exec',lambda self:pytest.fail('dialog must not open'))
    view._on_row_double_clicked(view._model.index(0,0))
    assert view._entries==[]

def test_sensitive_write_uses_the_same_timestamp_in_central_and_mirror(source,monkeypatch,tmp_path):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    calls=[]
    monkeypatch.setattr('sme_gend.config.sme_context.mois_actif_courant',lambda:'2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'SYNTHETIC')
    monkeypatch.setattr(audit,'_local_log_path',lambda:tmp_path/'mirror.jsonl')
    monkeypatch.setattr(audit,'_json_mirror_enabled',lambda:True)
    monkeypatch.setattr(AppConfigRepository,'append_audit',lambda self,**kw:calls.append(kw))
    audit._append_entry(action='dossier_diplome_add',personnel_id='12',payload=dict(actor='FORGED'))
    call=calls[0];entry=call['mirror'][1][0]
    assert call['ts_utc'].isoformat()==entry['ts_utc']
    assert call['actor']==entry['actor']=='SYNTHETIC'


def test_qt_real_background_worker_runs_sql_off_gui_thread(screen,monkeypatch):
    import time
    from PySide6.QtCore import QThread
    from sme_gend.ui.bg_task import run_in_background
    view,jobs,app,ui=screen;observed=[]
    def search(query,**kw):
        observed.append(QThread.currentThread()!=app.thread())
        return [dict(actor='SYNTHETIC WORKER')]
    monkeypatch.setattr(ui,'run_in_background',run_in_background)
    monkeypatch.setattr(ui,'search_sensitive_audit',search)
    apply=view._apply_entries
    def applied(entries,limit):
        observed.append(QThread.currentThread()==app.thread())
        apply(entries,limit)
    monkeypatch.setattr(view,'_apply_entries',applied)
    view.refresh()
    deadline=time.monotonic()+10
    while view._loading and time.monotonic()<deadline:
        app.processEvents();time.sleep(.001)
    assert not view._loading and observed==[True,True]
    assert view._entries==[dict(actor='SYNTHETIC WORKER')]
