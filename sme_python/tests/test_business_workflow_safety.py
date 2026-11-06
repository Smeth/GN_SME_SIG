"""Workflow failures must remain visible without erasing a user's current list."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QListWidgetItem

from sme_gend.config.settings import Settings
from sme_gend.services.workflow_task_service import WorkflowTaskError


@pytest.fixture
def panel(monkeypatch):
    from sme_gend.ui.views import workflow_status_panel as view
    application=QApplication.instance() or QApplication([])
    monkeypatch.setattr(view,'session_can_perform_action',lambda permission:True)
    monkeypatch.setattr(view,'build_workflow_alerts',lambda settings:[])
    monkeypatch.setattr(view,'confirm_yes_no',lambda *args,**kwargs:True)
    monkeypatch.setattr(view.QInputDialog,'getMultiLineText',lambda *args,**kwargs:('Vérification synthétique',True))
    monkeypatch.setattr(view,'run_with_progress',lambda parent,title,text,fn,**kwargs:fn())
    frame=view.WorkflowStatusPanel(Settings(database_path=Path('unused.mdb')))
    frame._task_svc=MagicMock()
    frame._fresh=True
    frame._alerts_list.addItem('OLD ALERT')
    row=QListWidgetItem('OLD TASK');row.setData(Qt.ItemDataRole.UserRole,7)
    row.setData(int(Qt.ItemDataRole.UserRole)+1, SimpleNamespace(id=7,status='open',detail='OLD DETAIL'))
    frame._tasks_list.addItem(row);frame._tasks_list.setCurrentRow(0)
    yield frame,view,application
    frame.deleteLater();application.processEvents()


def test_workflow_reload_failure_preserves_rows_selection_and_shows_status(panel):
    frame,view,app=panel
    frame._task_svc.list_open.side_effect=WorkflowTaskError('synthetic storage failure')
    frame.reload()
    assert frame._tasks_list.item(0).text()=='OLD TASK'
    assert frame._tasks_list.currentRow()==0
    assert frame._alerts_list.item(0).text()=='OLD ALERT'
    assert frame._tasks_status.text() and not frame._tasks_status.isHidden()


def test_workflow_successful_reload_replaces_rows_and_clears_error(panel):
    frame,view,app=panel
    frame._task_svc.list_open.return_value=[SimpleNamespace(id=8,title='NEW TASK',detail='DETAIL',mois='2026-09',priority='normal',status='open')]
    frame._tasks_status.setText('OLD FAILURE');frame._tasks_status.show()
    assert frame.reload() is True
    assert frame._tasks_list.count()==1 and 'NEW TASK' in frame._tasks_list.item(0).text()
    assert frame._tasks_status.text()=='' and frame._tasks_status.isHidden()


def test_workflow_resolve_failure_is_reported_without_erasing_or_reloading(panel,monkeypatch):
    frame,view,app=panel
    frame._task_svc.resolve_task.side_effect=RuntimeError('SYNTHETIC PRIVATE DETAIL')
    frame.reload=MagicMock();messages=[]
    monkeypatch.setattr(view.QMessageBox,'critical',lambda *args:messages.append(args[-1]))
    frame._on_resolve_task()
    assert len(messages)==1 and 'SYNTHETIC PRIVATE DETAIL' not in messages[0]
    assert frame._tasks_list.item(0).text()=='OLD TASK'
    frame.reload.assert_not_called()


def test_workflow_already_resolved_on_another_client_still_refreshes(panel):
    frame,view,app=panel
    frame._task_svc.resolve_task.return_value=False
    frame.reload_async=MagicMock(return_value=True)
    frame._on_resolve_task()
    frame.reload_async.assert_called_once()


def test_workflow_resolve_permission_is_still_enforced(panel,monkeypatch):
    frame,view,app=panel
    monkeypatch.setattr(view,'session_can_perform_action',lambda permission:False)
    frame._on_resolve_task()
    frame._task_svc.resolve_task.assert_not_called()


def test_preclosure_task_failure_is_not_swallowed(monkeypatch):
    from sme_gend.services import pre_closure_service as service
    failed=MagicMock();failed.sync_from_pre_closure.side_effect=WorkflowTaskError('synthetic failure')
    monkeypatch.setattr(service,'WorkflowTaskService',lambda settings:failed)
    with pytest.raises(WorkflowTaskError):
        service.finalize_pre_closure_report(Settings(database_path=Path('unused.mdb')),object())


@pytest.mark.parametrize('failed',[False,True])
def test_workflow_alerts_use_full_count_and_distinguish_storage_failure(monkeypatch,failed):
    from sme_gend.services import workflow_alert_service as alerts
    settings=Settings(database_path=Path('unused.mdb'))
    monkeypatch.setattr(alerts,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09',_month_snapshot=object()))
    workspace=SimpleNamespace(get_active_mois=lambda:'2026-09',is_month_closed=lambda month:False,list_closed_months=lambda:[])
    monkeypatch.setattr(alerts,'SmeWorkspaceService',lambda settings:workspace)
    monkeypatch.setattr(alerts,'check_mysql_connectivity',lambda settings:(True,'OK'))
    monkeypatch.setattr(alerts,'evaluate_editing_state',lambda settings:SimpleNamespace(locked=False,tabsme_statut='Ouvert'))
    monkeypatch.setattr(alerts,'find_latest_backup_zip',lambda:Path('synthetic.zip'))
    tasks=MagicMock()
    if failed:tasks.count_open.side_effect=RuntimeError('SYNTHETIC PRIVATE DETAIL')
    else:tasks.count_open.return_value=125
    tasks.list_open.side_effect=AssertionError('A limited page is not a total count')
    monkeypatch.setattr(alerts,'WorkflowTaskService',lambda settings:tasks)
    result=alerts.build_workflow_alerts(settings)
    if failed:
        assert any(item.alert_id=='tasks_unavailable' for item in result)
        assert not any('SYNTHETIC PRIVATE DETAIL' in item.detail for item in result)
    else:
        assert any(item.alert_id=='open_tasks' and '125' in item.title for item in result)



@pytest.mark.parametrize('accepted',[False,True])
def test_gain_gap_batch_starts_only_after_confirmation(monkeypatch,accepted):
    from sme_gend.ui.views import gain_perte_view as view
    from sme_gend.services import auth_guard
    events=[];repository=MagicMock()
    repository.insert_rows.side_effect=lambda rows:events.append('write')
    fake=SimpleNamespace(_settings=Settings(database_path=Path('unused.mdb')),
        _editing_locked_for_current_mois=lambda:False,_gap_rows=['synthetic row'],
        _repo=repository,_reload_after_change=MagicMock())
    monkeypatch.setattr(auth_guard,'require_permission',lambda *args,**kwargs:True)
    def confirm(*args,**kwargs):events.append('confirm');return accepted
    monkeypatch.setattr(view,'confirm_yes_no',confirm)
    view.GainPerteView._on_record_gaps(fake)
    assert events==(['confirm','write'] if accepted else ['confirm'])
    if accepted:repository.insert_rows.assert_called_once_with(['synthetic row'])
    else:repository.insert_rows.assert_not_called()


def test_task_failure_warns_without_changing_the_existing_closure_confirmation(monkeypatch):
    from PySide6.QtWidgets import QWidget,QDialog
    from sme_gend.ui.views import sme_workflow_panel as view
    application=QApplication.instance() or QApplication([])
    frame=QWidget();frame._settings=Settings(database_path=Path('unused.mdb'))
    monkeypatch.setattr(view,'session_can_perform_action',lambda permission:True)
    monkeypatch.setattr(view,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09',_month_snapshot=object()))
    monkeypatch.setattr(QDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    report=SimpleNamespace(checks=[SimpleNamespace(level='warn',title='CHECK',detail='DETAIL')],blocking=[])
    monkeypatch.setattr(view,'run_pre_closure_checks',lambda *args:report)
    def failed(*args):raise WorkflowTaskError('synthetic failure')
    monkeypatch.setattr(view,'finalize_pre_closure_report',failed)
    warnings=[];confirmation=MagicMock(return_value=False)
    monkeypatch.setattr(view.QMessageBox,'warning',lambda *args:warnings.append(args[-1]))
    monkeypatch.setattr(view,'confirm_destructive_with_ack',confirmation)
    monkeypatch.setattr(view,'run_with_progress',lambda parent,title,text,fn,**kwargs:fn())
    monkeypatch.setattr(view,'run_piste_a_month_closure',lambda *args,**kwargs:pytest.fail('Closure not confirmed'))
    view.SmeWorkflowPanel.open_closure_dialog(frame)
    assert len(warnings)==1
    confirmation.assert_called_once()
    frame.deleteLater();application.processEvents()



@pytest.mark.parametrize('storage_failed',[False,True])
def test_blocking_closure_checks_are_recorded_as_tasks_without_allowing_closure(monkeypatch,storage_failed):
    from PySide6.QtWidgets import QWidget,QDialog
    from sme_gend.ui.views import sme_workflow_panel as view
    application=QApplication.instance() or QApplication([])
    frame=QWidget();frame._settings=Settings(database_path=Path('unused.mdb'))
    monkeypatch.setattr(view,'session_can_perform_action',lambda permission:True)
    monkeypatch.setattr(view,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09',_month_snapshot=object()))
    monkeypatch.setattr(QDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    check=SimpleNamespace(level='block',title='BLOCKING CHECK',detail='DETAIL')
    report=SimpleNamespace(checks=[check],blocking=[check])
    monkeypatch.setattr(view,'run_pre_closure_checks',lambda *args:report)
    record=MagicMock()
    if storage_failed:record.side_effect=WorkflowTaskError('synthetic tracking failure')
    monkeypatch.setattr(view,'finalize_pre_closure_report',record)
    warnings=[];critical=[];confirmation=MagicMock()
    monkeypatch.setattr(view.QMessageBox,'warning',lambda *args:warnings.append(args[-1]))
    monkeypatch.setattr(view.QMessageBox,'critical',lambda *args:critical.append(args[-1]))
    monkeypatch.setattr(view,'confirm_destructive_with_ack',confirmation)
    monkeypatch.setattr(view,'run_with_progress',lambda parent,title,text,fn,**kwargs:fn())
    monkeypatch.setattr(view,'run_piste_a_month_closure',lambda *args,**kwargs:pytest.fail('Blocked month cannot close'))
    view.SmeWorkflowPanel.open_closure_dialog(frame)
    record.assert_called_once_with(frame._settings,report)
    confirmation.assert_not_called()
    assert len(critical)==1 and len(warnings)==int(storage_failed)
    frame.deleteLater();application.processEvents()



def test_workflow_resolve_transmits_the_displayed_version_and_preserves_conflict(panel,monkeypatch):
    from sme_gend.services.workflow_task_service import WorkflowTaskRow,WorkflowTaskConflictError
    frame,view,app=panel
    row=WorkflowTaskRow(7,'2026-09','manual','open','normal','SYNTHETIC','DETAIL',None,None,write_token='synthetic')
    frame._task_svc.list_open.return_value=[row]
    frame.reload();frame._tasks_list.setCurrentRow(0)
    frame.reload=MagicMock();messages=[]
    frame._task_svc.resolve_task.side_effect=WorkflowTaskConflictError('synthetic conflict')
    monkeypatch.setattr(view,'show_reference_conflict',lambda *args:messages.append(args[-1]))
    frame._on_resolve_task()
    frame._task_svc.resolve_task.assert_called_once_with(7,expected=row,comment='Vérification synthétique',reopen=False)
    assert frame._tasks_list.currentRow()==0 and len(messages)==1
    frame.reload.assert_not_called()
    assert not frame._tasks_status.isHidden()
