"""Recette du cycle et des archives sur fichiers et périodes synthétiques."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget
from PySide6.QtGui import QFontDatabase
from sme_gend.config.settings import Settings
from sme_gend.services.archive_catalog import archive_month, scan_archive_directory
from sme_gend.services.pre_closure_service import PreClosureCheck, PreClosureReport


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    return instance


@pytest.fixture
def settings():
    return Settings(database_path=Path('unused.mdb'), mysql_view_mois='2026-09')


@pytest.mark.parametrize('name,expected', [('mysql_2026-09.sql','2026-09'), ('layout_2026_01.json','2026-01'),
    ('file_2026-13.sql',None), ('file_2026-00.sql',None), ('backup.zip',None)])
def test_archive_month_is_a_real_calendar_month(name,expected):
    assert archive_month(name)==expected


def test_archive_scan_does_not_create_missing_directory(tmp_path):
    directory=tmp_path/'absent'
    result=scan_archive_directory(directory)
    assert result.missing and result.entries==()
    assert not directory.exists()


def test_archive_scan_recognizes_mysql_access_layout_and_complete_backups(tmp_path):
    for name in ['mysql_2026-09.sql','layout_2026-09.json','sme_2026-01.mdb','backup.zip','partial.tmp','random.bin']:
        (tmp_path/name).write_bytes(b'SYNTHETIC')
    result=scan_archive_directory(tmp_path)
    assert {entry[0].suffix for entry in result.entries}=={'.sql','.json','.mdb','.zip'}


@pytest.fixture
def archives(app,tmp_path,monkeypatch):
    from sme_gend.ui.views import archives_view as ui
    monkeypatch.setattr(ui,'sme_archives_dir',lambda:tmp_path)
    monkeypatch.setattr(ui,'require_permission',lambda *args:True)
    monkeypatch.setattr(ui,'session_has_permission',lambda *args:True)
    monkeypatch.setattr(ui,'session_can_perform_action',lambda *args:True)
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:done(work()))
    for name in ['mysql_2026-09.sql','layout_2026-09.json','sme_2026-01.mdb','backup.zip']:
        (tmp_path/name).write_bytes(b'SYNTHETIC')
    view=ui.ArchivesView()
    yield view,ui
    view.close()


def test_archive_filters_combine_and_refresh_preserves_user_month(archives):
    view,ui=archives
    view.refresh_list()
    assert view._table.rowCount()==4
    view.set_active_mois('2026-09')
    view._month_filter.setCurrentIndex(view._month_filter.findData('2026-01'))
    view.refresh_list()
    assert view._month_filter.currentData()=='2026-01'
    assert view._table.rowCount()==1
    view._type_filter.setCurrentIndex(view._type_filter.findData('Sauvegarde MySQL'))
    assert view._table.rowCount()==0
    assert 'filtres' in view._status.text()
    view._month_filter.setCurrentIndex(0)
    view._search.setText('MYSQL')
    assert view._table.rowCount()==1


def test_archive_open_uses_selected_path_and_never_executes_sql(archives,monkeypatch):
    view,ui=archives
    opened=MagicMock(return_value=True)
    monkeypatch.setattr(ui.QDesktopServices,'openUrl',opened)
    information=MagicMock()
    monkeypatch.setattr(ui.QMessageBox,'information',information)
    monthly=MagicMock()
    monkeypatch.setattr(view,'_monthly_transfer',monthly)
    view.refresh_list()
    view._type_filter.setCurrentIndex(view._type_filter.findData('Archive Access'))
    view._table.selectRow(0)
    path=view._selected_path()
    assert view._import.isEnabled()
    view._open_selected()
    assert Path(opened.call_args.args[0].toLocalFile())==path
    opened.reset_mock()
    view._type_filter.setCurrentIndex(view._type_filter.findData('Sauvegarde MySQL'))
    view._table.selectRow(0)
    assert not view._import.isEnabled()
    view._open_selected()
    opened.assert_not_called()
    monthly.assert_called_once_with(initial_source=view._selected_path())


def test_archive_stale_result_and_permission_denial_cannot_publish(archives,monkeypatch,tmp_path):
    view,ui=archives
    callbacks=[]
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:callbacks.append(done))
    view.refresh_list()
    view.mark_dirty()
    callbacks[0](scan_archive_directory(tmp_path))
    assert view._table.rowCount()==0
    view.refresh_list()
    monkeypatch.setattr(ui,'session_has_permission',lambda *args:False)
    callbacks[1](scan_archive_directory(tmp_path))
    assert view._table.rowCount()==0
    assert 'autorisée' in view._status.text()


def test_archive_error_has_explicit_status(archives,monkeypatch):
    view,ui=archives
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:kw['on_error'](OSError('synthetic')))
    view.refresh_list()
    assert 'impossible' in view._status.text()
    assert view._view_data.needs_load()
    assert view._refresh.isEnabled()


def test_zip_archive_opens_existing_restore_page_without_restoring(archives):
    from sme_gend.ui.navigation_pages import PAGE_BACKUP_RESTORE
    view,ui=archives
    view.refresh_list()
    view._type_filter.setCurrentIndex(view._type_filter.findData('Archive ZIP (à vérifier)'))
    view._table.selectRow(0)
    view._go_page=MagicMock()
    view._open_selected()
    view._go_page.assert_called_once_with(PAGE_BACKUP_RESTORE)


@pytest.fixture
def tasks(app,settings,monkeypatch):
    from sme_gend.ui.views import workflow_status_panel as ui
    monkeypatch.setattr(ui,'session_can_perform_action',lambda *args:True)
    monkeypatch.setattr(ui,'build_workflow_alerts',lambda *args:[])
    view=ui.WorkflowStatusPanel(settings)
    view._task_svc=MagicMock()
    view._task_svc.list_open.return_value=[]
    yield view,ui
    view.close()


def task(identifier,month='2026-09',priority='normal',title='Tâche à vérifier'):
    return SimpleNamespace(id=identifier,mois=month,priority=priority,title=title,detail='Détail synthétique',status='open',resolved_by=None,resolved_at=None)


def test_task_empty_states_filters_and_full_list(tasks):
    view,ui=tasks
    assert view.reload()
    assert 'Aucune' in view._alerts_empty.text()
    assert 'Aucune tâche' in view._task_count.text()
    view._task_svc.list_open.return_value=[task(i) for i in range(250)]+[task(999,'2026-01','high','Contrôle ÉTÉ')]
    view.reload()
    view._task_svc.list_open.assert_called_with(limit=None,status=None)
    assert view._tasks_list.count()==251
    view._search.setText('ete')
    assert view._tasks_list.count()==1
    view._priority.setCurrentIndex(view._priority.findData('high'))
    view._tasks_list.setCurrentRow(0)
    assert view._detail.toPlainText()=='Détail synthétique'
    assert view._btn_resolve.isEnabled()
    view._month.setCurrentIndex(view._month.findData('2026-09'))
    assert view._tasks_list.count()==0 and not view._btn_resolve.isEnabled()


def test_task_stale_response_is_rejected(tasks,monkeypatch):
    view,ui=tasks
    callbacks=[]
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:callbacks.append(done))
    view.reload_async()
    view.mark_dirty()
    callbacks[0](([task(1)],[]))
    assert view._tasks_list.count()==0 and not view._fresh


def test_task_permissions_are_rechecked_when_background_read_finishes(tasks,monkeypatch):
    view,ui=tasks
    pending=[]
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:pending.append(done))
    view.reload_async()
    monkeypatch.setattr(ui,'session_has_permission',lambda *args:False)
    pending[0](([task(1)],[]))
    assert view._tasks_list.count()==0 and not view._fresh
    assert 'autorisée' in view._tasks_status.text()


def test_task_context_change_during_confirmation_prevents_write(tasks,monkeypatch):
    view,ui=tasks
    view._task_svc.list_open.return_value=[task(1)]
    view.reload()
    view._tasks_list.setCurrentRow(0)
    def changed(*args,**kwargs):
        view.mark_dirty()
        return True
    monkeypatch.setattr(ui,'confirm_yes_no',changed)
    view._on_resolve_task()
    view._task_svc.resolve_task.assert_not_called()


def test_task_confirmation_cancel_and_stale_rows_cannot_write(tasks,monkeypatch):
    view,ui=tasks
    view._task_svc.list_open.return_value=[task(1)]
    view.reload()
    view._tasks_list.setCurrentRow(0)
    monkeypatch.setattr(ui,'confirm_yes_no',lambda *args,**kw:False)
    view._on_resolve_task()
    view._task_svc.resolve_task.assert_not_called()
    view._fresh=False
    monkeypatch.setattr(ui,'confirm_yes_no',lambda *args,**kw:pytest.fail('Stale task must not be confirmed'))
    view._on_resolve_task()


@pytest.fixture
def workflow(app,settings,monkeypatch):
    from sme_gend.ui.views import sme_workflow_panel as ui
    monkeypatch.setattr(ui,'session_can_perform_action',lambda *args:True)
    view=ui.SmeWorkflowPanel(settings)
    yield view,ui
    view.close()


def test_period_filters_selection_and_consultation_use_existing_navigation(workflow):
    view,ui=workflow
    rows=[SimpleNamespace(codsme='1',mois='2026-09',statut='Ouvert'),SimpleNamespace(codsme='2',mois='2026-01',statut='Fermé')]
    view.update_sme_table(SimpleNamespace(sme_table_ok=True,sme_rows=rows))
    view._period_filter.setCurrentIndex(view._period_filter.findData('ferme'))
    assert view._sme_table.rowCount()==1
    view._sme_table.selectRow(0)
    view._activate_period=MagicMock()
    view._consult_period()
    view._activate_period.assert_called_once_with('2026-01')


def test_export_access_uses_valid_progress_api_and_handles_failure(workflow,tmp_path,monkeypatch):
    view,ui=workflow
    monkeypatch.setattr(ui,'load_sme_context',lambda:SimpleNamespace(mois_actif='2026-09'))
    monkeypatch.setattr(QDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    monkeypatch.setattr('sme_gend.services.export_report.default_export_directory_hint',lambda:str(tmp_path))
    exporter=MagicMock()
    exporter.return_value.to_dict.return_value={'output_path':str(tmp_path/'synthetic.mdb')}
    monkeypatch.setattr('sme_gend.services.mysql_bundle_export_to_access.export_mysql_bundle_to_access',exporter)
    progress=MagicMock(side_effect=lambda parent,title,total,fn:(fn(lambda *_:None),None)[1])
    monkeypatch.setattr(ui,'run_with_determinate_progress',progress)
    view._on_sme_export_access_dialog()
    assert progress.call_args.args[2]==5
    exporter.assert_called_once()
    assert exporter.call_args.kwargs['mois']=='2026-09'


def test_archives_button_opens_the_files_tab(workflow):
    view,ui=workflow
    view._cycle_archives_view=SimpleNamespace(show_archives_tab=MagicMock())
    view._on_go_archives_page()
    view._cycle_archives_view.show_archives_tab.assert_called_once()


def test_cycle_tabs_load_the_page_that_was_selected(app):
    from sme_gend.ui.views.cycle_archives_view import CycleArchivesView
    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.on_page_activated=MagicMock()
    pilotage,archives,followup=Page(),Page(),Page()
    pilotage._workflow_status=followup
    view=CycleArchivesView(pilotage,archives)
    view._tabs.setCurrentIndex(view.TAB_TASKS)
    followup.on_page_activated.assert_called_once()
    view._tabs.setCurrentIndex(view.TAB_ARCHIVES)
    archives.on_page_activated.assert_called_once()
    view.close()


@pytest.mark.parametrize('permission,visible', [('system.archives',(False,False,True)),('workflow.pilotage',(True,True,False))])
def test_cycle_tab_permissions_separate_files_and_workflow(app,monkeypatch,permission,visible):
    from sme_gend.ui.views.cycle_archives_view import CycleArchivesView
    auth=SimpleNamespace(is_enforced=lambda:True,session_has_permission=lambda value:value==permission)
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth',lambda:auth)
    pilotage,archives,followup=QWidget(),QWidget(),QWidget()
    pilotage._workflow_status=followup
    view=CycleArchivesView(pilotage,archives)
    assert tuple(view._tabs.isTabVisible(i) for i in range(3))==visible
    view.close()


def test_preclosure_report_has_french_levels_and_actual_months(app):
    from sme_gend.ui.pre_closure_report_dialog import PreClosureReportDialog
    from PySide6.QtWidgets import QTableWidget
    report=PreClosureReport('2026-09','2026-10',(PreClosureCheck('x','block','Contrôle','Une incohérence'),))
    view=PreClosureReportDialog(None,report)
    table=view.findChild(QTableWidget)
    assert table.item(0,0).text()=='Bloquant'
    assert table.item(0,2).text()=='Une incohérence'
    view.close()


def test_cycle_load_does_not_use_dashboard_or_publish_an_old_configuration(app,settings,monkeypatch):
    from sme_gend.ui.views import month_pilotage_view as ui
    pending=[]
    monkeypatch.setattr(ui,'run_in_background',lambda owner,work,done,**kw:pending.append(done))
    dashboard=MagicMock()
    view=ui.MonthPilotageView(settings,dashboard)
    view._reload(silent=True)
    view.mark_dirty()
    pending[0]((SimpleNamespace(sme_rows=(),sme_table_ok=True,active_month='2026-01'),0,None))
    assert view._stats_dirty
    assert '2026-01' not in view._load_status.text()
    dashboard.snapshot.assert_not_called()
    view.close()


def test_configuration_change_reloads_the_current_tasks_tab(app,settings,monkeypatch):
    from sme_gend.ui.views import month_pilotage_view as month, workflow_status_panel as tasks
    from sme_gend.ui.views.archives_view import ArchivesView
    from sme_gend.ui.views.cycle_archives_view import CycleArchivesView
    pending=[]
    monkeypatch.setattr(tasks,'run_in_background',lambda owner,work,done,**kw:pending.append(done))
    monkeypatch.setattr(tasks,'WorkflowTaskService',lambda settings:MagicMock())
    pilot=month.MonthPilotageView(settings,MagicMock())
    files=ArchivesView()
    view=CycleArchivesView(pilot,files)
    view.show()
    view._tabs.setCurrentIndex(view.TAB_TASKS)
    view.apply_runtime_settings(replace(settings,mysql_database='synthetic_new'))
    pending[-1](([task(1)],[]))
    assert pilot._workflow_status._fresh
    assert pilot._workflow_status._tasks_list.count()==1
    assert pilot._workflow_status._btn_refresh.isEnabled()
    view.close()
