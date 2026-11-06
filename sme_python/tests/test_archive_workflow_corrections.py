"""Régressions du cycle mensuel, sans connexion aux données réelles."""
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import csv
import io
import zipfile
import pytest
from PySide6.QtWidgets import QApplication
from sme_gend.config.settings import Settings
from sme_gend.services import pre_closure_service as pre


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def prechecks(monkeypatch):
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    listing = MagicMock()
    listing.fetch_all.return_value = [SimpleNamespace(position_admin="Présent")]
    factory = MagicMock(return_value=listing)
    gains = MagicMock()
    gains.fetch_for_month.return_value = [object()]
    for name, value in {
        "load_sme_context": lambda: SimpleNamespace(mois_actif="2026-01"),
        "SmeWorkspaceService": lambda _: SimpleNamespace(get_active_mois=lambda:"2026-01"),
        "SmeRepository": lambda _: SimpleNamespace(table_exists=lambda:True,
            fetch_all=lambda:[SimpleNamespace(mois="2026-01", statut="Ouvert")]),
        "ListeGeneraleRepository": factory, "GainPerteRepository": lambda _: gains,
        "evaluate_editing_state": lambda *a,**kw:SimpleNamespace(locked=False,mois_actif="2026-01"),
        "padmin_counts_as_present": lambda *a,**kw:True,
        "check_mysql_connectivity": lambda _:(True,"OK"),
        "find_latest_backup_zip": lambda:None, "find_archive_for_month":lambda _:None,
    }.items():
        monkeypatch.setattr(pre, name, value)
    monkeypatch.setattr("sme_gend.services.gain_perte_coherence.load_unsignaled_pertes", lambda *a:[])
    return settings, listing, factory, gains


def test_preclosure_uses_requested_month_and_keeps_original_settings(prechecks):
    settings, listing, factory, gains = prechecks
    assert pre.run_pre_closure_checks(settings,"2026-01","2026-02").can_proceed
    assert factory.call_args.args[0].mysql_view_mois == "2026-01"
    assert settings.mysql_view_mois == "2026-09"
    gains.fetch_for_month.assert_called_once_with("2026-01")


@pytest.mark.parametrize("source,target", [("2026-01","2025-12"),("2026-01","2026-01"),
    ("2026-13","2027-01"),("2026-01",""),("invalid","2026-02")])
def test_invalid_chronology_blocks_before_any_personnel_read(prechecks,source,target):
    settings, _, factory, _ = prechecks
    assert not pre.run_pre_closure_checks(settings,source,target).can_proceed
    factory.assert_not_called()


@pytest.mark.parametrize("failure,expected", [("liste","liste_error"),("movements","gain_perte_error"),("coherence","perte_coherence_error")])
def test_unavailable_required_checks_block(prechecks,monkeypatch,failure,expected):
    settings, listing, _, gains = prechecks
    if failure == "liste": listing.fetch_all.side_effect = OSError("synthetic")
    elif failure == "movements": gains.fetch_for_month.side_effect = OSError("synthetic")
    else:
        monkeypatch.setattr("sme_gend.services.gain_perte_coherence.load_unsignaled_pertes", MagicMock(side_effect=OSError("synthetic")))
    report = pre.run_pre_closure_checks(settings,"2026-01","2026-02")
    assert not report.can_proceed
    assert any(c.check_id == expected and c.level == "block" for c in report.checks)


def test_archive_read_failure_survives_filter_and_disables_actions(app,tmp_path,monkeypatch):
    from sme_gend.ui.views import archives_view as ui
    from sme_gend.services.archive_catalog import scan_archive_directory
    (tmp_path/"mysql_2026-01.sql").write_text("synthetic")
    monkeypatch.setattr(ui,"sme_archives_dir",lambda:tmp_path)
    monkeypatch.setattr(ui,"require_permission",lambda *a:True)
    monkeypatch.setattr(ui,"session_has_permission",lambda *a:True)
    monkeypatch.setattr(ui,"run_in_background",lambda owner,work,done,**kw:done(scan_archive_directory(tmp_path)))
    view = ui.ArchivesView()
    view.refresh_list()
    view._table.selectRow(0)
    monkeypatch.setattr(ui,"run_in_background",lambda owner,work,done,**kw:kw["on_error"](OSError()))
    view.refresh_list()
    view._search.setText("mysql")
    assert "impossible" in view._status.text()
    assert not view._open.isEnabled() and not view._verify.isEnabled()
    view.close()


def test_periods_cleared_after_rebind_and_failed_reload(app,monkeypatch):
    from sme_gend.ui.views import month_pilotage_view as ui
    pending = []
    monkeypatch.setattr(ui,"run_in_background",lambda owner,work,done,**kw:pending.append(kw["on_error"]))
    settings = Settings(database_path=Path("unused"))
    view = ui.MonthPilotageView(settings,MagicMock())
    view._sme_panel.update_sme_table(SimpleNamespace(sme_table_ok=True,
        sme_rows=[SimpleNamespace(codsme="OLD",mois="2026-01",statut="Fermé")]))
    view._sme_panel._sme_table.selectRow(0)
    view.apply_runtime_settings(replace(settings,mysql_database="synthetic_other"))
    view._reload()
    pending[-1](OSError())
    assert view._sme_panel._sme_table.rowCount() == 0
    assert not view._sme_panel._btn_consult_period.isEnabled()
    view.close()


def test_empty_month_button_requires_admin(app,monkeypatch):
    from sme_gend.ui.views import archives_view as ui
    monkeypatch.setattr(ui,"session_can_perform_action",lambda *a:True)
    monkeypatch.setattr(ui,"session_has_admin_role",lambda:False)
    view = ui.ArchivesView()
    assert not view._empty.isEnabled()
    view.close()


def test_archive_verification_does_not_certify_an_empty_or_arbitrary_zip(tmp_path):
    from sme_gend.services.archive_catalog import inspect_archive
    path = tmp_path/"backup_2026-01.zip"
    path.write_bytes(b"")
    with pytest.raises(ValueError): inspect_archive(path)
    with zipfile.ZipFile(path,"w") as archive: archive.writestr("random.txt","synthetic")
    before = path.read_bytes()
    with pytest.raises(ValueError,match="manifest"): inspect_archive(path)
    assert path.read_bytes() == before


def test_sql_verification_is_explicitly_not_execution_or_certification(tmp_path):
    from sme_gend.services.archive_catalog import inspect_archive
    path = tmp_path/"mysql_2026-01.sql"
    path.write_text("SYNTHETIC")
    result = inspect_archive(path)
    assert "SHA-256" in result and "non certifié" in result and "non vérifiée" in result


def test_report_export_escapes_spreadsheet_formulas():
    from sme_gend.ui.pre_closure_report_dialog import report_csv
    report = pre.PreClosureReport("2026-01","2026-02",(pre.PreClosureCheck("test","warn","=1+1","@synthetic"),))
    rows = list(csv.reader(io.StringIO(report_csv(report)),delimiter=";"))
    assert rows[1][3:] == ["'=1+1","'@synthetic"]


@pytest.fixture
def task_store(monkeypatch):
    from sme_gend.services import workflow_task_service as service
    obj = service.WorkflowTaskService(Settings(database_path=Path("unused")))
    obj._repo = MagicMock()
    obj._ensure_table = lambda: True
    obj._lock_revision = MagicMock()
    obj.create_task = MagicMock()
    cursor = obj._repo._mc.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.rowcount = 1
    monkeypatch.setattr(service,"shared_data_write",lambda *a,**kw:nullcontext())
    monkeypatch.setattr(service,"mark_shared_data_write",MagicMock())
    monkeypatch.setattr(service,"resolve_audit_actor",lambda:"synthetic")
    audit = MagicMock()
    monkeypatch.setattr("sme_gend.services.business_audit.record_business_action",audit)
    return obj, cursor, audit


def test_repeated_control_updates_detail_and_priority_without_duplicate(task_store):
    obj, cursor, audit = task_store
    cursor.fetchall.return_value = [("pre_closure_test","Contrôle",7,"Ancien","normal","operateur","open")]
    obj.sync_from_pre_closure(pre.PreClosureReport("2026-01","2026-02",(pre.PreClosureCheck("test","block","Contrôle","Nouveau"),)))
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")]
    assert len(updates) == 1 and updates[0].args[1] == ("Nouveau","high","admin",7)
    obj.create_task.assert_not_called()
    assert audit.call_args.kwargs["action"] == "workflow_task_update"


@pytest.mark.parametrize("unavailable",[False,True])
def test_disappeared_finding_requires_revalidation_only_after_complete_control(task_store,unavailable):
    obj,cursor,audit = task_store
    cursor.fetchall.return_value = [("pre_closure_old","Ancien",7,"Détail","normal","operateur","open")]
    checks = (pre.PreClosureCheck("liste_error","block","Indisponible","Erreur"),) if unavailable else ()
    obj.sync_from_pre_closure(pre.PreClosureReport("2026-01","2026-02",checks))
    updates = [c for c in cursor.execute.call_args_list if c.args[0].startswith("UPDATE")]
    assert bool(updates) is not unavailable
    if updates: assert "status='review'" in updates[0].args[0]


def test_task_history_filter_and_reactivation_ui(app,monkeypatch):
    from sme_gend.ui.views import workflow_status_panel as ui
    from sme_gend.services.workflow_task_service import WorkflowTaskRow
    monkeypatch.setattr(ui,"session_can_perform_action",lambda *a:True)
    monkeypatch.setattr(ui,"session_has_permission",lambda *a:True)
    monkeypatch.setattr(ui,"confirm_yes_no",lambda *a,**kw:True)
    monkeypatch.setattr(ui.QInputDialog,"getMultiLineText",lambda *a,**kw:("Après contrôle",True))
    monkeypatch.setattr(ui,"run_with_progress",lambda owner,title,text,work:work())
    view = ui.WorkflowStatusPanel(Settings(database_path=Path("unused")))
    view._task_svc = MagicMock()
    row = WorkflowTaskRow(7,"2026-01","manual","done","normal","Contrôle","Détail",None,None,
                          resolved_by="testeur",resolved_at="2026-01-31")
    view._apply_result([row],[])
    assert view._tasks_list.count() == 0
    view._state.setCurrentIndex(view._state.findData("done"))
    view._tasks_list.setCurrentRow(0)
    assert "testeur" in view._detail.toPlainText()
    assert "Réactiver" in view._btn_resolve.text()
    view.reload_async = MagicMock()
    view._on_resolve_task()
    view._task_svc.resolve_task.assert_called_once_with(7,expected=row,comment="Après contrôle",reopen=True)
    view.close()


@pytest.mark.parametrize("accept",[False, True])
def test_restore_access_preview_and_confirmation_never_close_or_switch_month(app,tmp_path,monkeypatch,accept):
    from PySide6.QtWidgets import QWidget
    from sme_gend.ui import import_liste_generale_wizard as ui
    from sme_gend.ui import import_mois_dialog
    path = tmp_path/"sme_2026-01.mdb"
    path.write_bytes(b"SYNTHETIC")
    monkeypatch.setattr("sme_gend.services.workflow_permissions.session_can_perform_action",lambda *a:True)
    monkeypatch.setattr("sme_gend.services.access_odbc.preflight_access_file",lambda *a,**kw:(True,"OK"))
    monkeypatch.setattr(import_mois_dialog,"pick_import_mois",lambda *a,**kw:"2026-01")
    monkeypatch.setattr(ui,"pick_import_options",lambda *a,**kw:pytest.fail("La restauration ne propose pas de clôture"))
    monkeypatch.setattr(ui.QFileDialog,"getOpenFileName",lambda *a,**kw:pytest.fail("Le fichier sélectionné doit être conservé"))
    monkeypatch.setattr("sme_gend.services.mysql_bundle_import.preview_access_import",lambda *a:{"counts":{"ListeGenerale":3},"warnings":[]})
    target = MagicMock()
    target.fetch_all.return_value = [object(),object()]
    monkeypatch.setattr("sme_gend.repositories.liste_generale_repository.ListeGeneraleRepository",lambda settings:target)
    from sme_gend.services.import_replacement import ImportReplacementPreview
    monkeypatch.setattr("sme_gend.services.import_replacement.preview_import_replacement",
                        lambda *a, **kw: ImportReplacementPreview("2026-01", True, (), "test", (("carriere_situation", 2),)))
    monkeypatch.setattr(ui,"run_with_progress",lambda parent,title,text,work:work())
    monkeypatch.setattr(ui,"run_with_determinate_progress",lambda parent,title,total,work:work(lambda *a:None))
    monkeypatch.setattr(ui,"load_sme_context",lambda:SimpleNamespace(mois_actif="2026-09"))
    confirmation = MagicMock(return_value=accept)
    monkeypatch.setattr(ui,"confirm_yes_no",confirmation)
    importer = MagicMock(return_value=SimpleNamespace(mysql_bundle_result=None,data_archive=None,warnings=[]))
    monkeypatch.setattr("sme_gend.services.sme_month_closure.import_existing_db_for_month",importer)
    monkeypatch.setattr("sme_gend.ui.confirm_dialogs.show_scrollable_html_message",lambda *a:None)
    parent = QWidget()
    settings = Settings(database_path=Path("unused"))
    assert ui.run_import_liste_generale_from_access(parent,settings,initial_mdb=path,restore_period=True) is accept
    assert "2 agent(s)" in confirmation.call_args.args[2]
    if accept:
        assert importer.call_args.args[1:4] == (path,"2026-01",None)
        assert importer.call_args.kwargs["archive_and_close"] is False
    else:
        importer.assert_not_called()
    parent.close()


def test_reactivation_checks_version_and_clears_resolution_metadata(task_store):
    from sme_gend.services.workflow_task_service import WorkflowTaskRow
    from sme_gend.repositories.mysql_connection import mysql_database_key
    obj,cursor,audit = task_store
    row = WorkflowTaskRow(7,"2026-01","manual","done","normal","Contrôle","Détail",None,None,
                          write_token="workflow_task:7:"+"a"*64, _write_database=mysql_database_key(obj._settings))
    cursor.fetchone.return_value = ("done", "a"*64)
    assert obj.resolve_task(7,expected=row,reopen=True,comment="Réexamen")
    update = [c for c in cursor.execute.call_args_list if c.args[0].lstrip().startswith("UPDATE")][0]
    assert update.args[1] == ("open",None,None,7,"done")
    assert audit.call_args.kwargs["action"] == "workflow_task_reopen"
    assert audit.call_args.kwargs["after"]["comment"] == "Réexamen"


def test_reactivation_refuses_stale_version_and_missing_reason(task_store):
    from sme_gend.services.workflow_task_service import WorkflowTaskRow, WorkflowTaskConflictError
    from sme_gend.repositories.mysql_connection import mysql_database_key
    obj,cursor,audit = task_store
    row = WorkflowTaskRow(7,"2026-01","manual","done","normal","Contrôle","Détail",None,None,
                          write_token="workflow_task:7:"+"a"*64, _write_database=mysql_database_key(obj._settings))
    with pytest.raises(ValueError): obj.resolve_task(7,expected=row,reopen=True)
    cursor.fetchone.return_value = ("done","b"*64)
    with pytest.raises(WorkflowTaskConflictError): obj.resolve_task(7,expected=row,reopen=True,comment="Réexamen")
    assert not any(c.args[0].lstrip().startswith("UPDATE") for c in cursor.execute.call_args_list)
    audit.assert_not_called()


def test_task_audit_search_filters_entity_in_sql_before_limit():
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    repo = AppConfigRepository(Settings(database_path=Path("unused")))
    repo.table_exists = lambda *a:True
    repo._mc = MagicMock()
    cursor = repo._mc.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = []
    assert repo.search_audit_journal(module="workflow",entity_id="7",limit=2000) == []
    sql,params = cursor.execute.call_args.args
    assert "JSON_EXTRACT(payload, '$.entity_id')" in sql
    assert params == ("7","workflow",2000)


def test_closure_record_separates_prechecks_and_real_closures(monkeypatch):
    from sme_gend.ui import closure_record_dialog as ui
    monkeypatch.setattr(ui,"session_has_permission",lambda *a:True)
    repo = MagicMock()
    repo.search_audit_journal.side_effect = [
        [{"payload":{"mois_cloture":"2026-01"}},{"payload":{"mois_cloture":"2026-02"}}],
        [{"payload":{"mois_importe":"2026-01","tabsme_rows_closed":1}},
         {"payload":{"mois_importe":"2026-01","tabsme_rows_closed":0}}],
        [{"action":"sme_preclosure_report","payload":{"report":{}}}],
    ]
    monkeypatch.setattr(ui,"AppConfigRepository",lambda settings:repo)
    result = ui.load_closure_record(Settings(database_path=Path("unused")),"2026-01")
    assert len(result) == 3
    assert result[-1]["action"] == "sme_preclosure_report"


def test_control_report_is_saved_with_task_batch_and_failure_propagates(task_store):
    from sme_gend.services.workflow_task_service import WorkflowTaskError
    obj,cursor,audit = task_store
    cursor.fetchall.return_value = []
    report = pre.PreClosureReport("2026-01","2026-02",())
    obj.sync_from_pre_closure(report)
    args = obj._repo.append_audit.call_args.kwargs
    assert args["sme_mois_context"] == "2026-01"
    assert args["payload"]["report"]["mois_suivant"] == "2026-02"
    obj._repo.append_audit.side_effect = RuntimeError("synthetic journal failure")
    with pytest.raises(WorkflowTaskError): obj.sync_from_pre_closure(report)


def test_successful_closure_includes_full_control_report_in_existing_audit(tmp_path,monkeypatch):
    from sme_gend.services import sme_month_closure as service
    settings = Settings(database_path=tmp_path/"missing.mdb")
    sql = tmp_path/"mysql_2026-01.sql"
    sql.write_text("SYNTHETIC")
    monkeypatch.setattr(service,"sme_archives_dir",lambda:tmp_path)
    monkeypatch.setattr(service,"_snapshot_layout_from_central",lambda *a:None)
    monkeypatch.setattr("sme_gend.services.mysql_backup_service.backup_mysql_database",lambda *a:(sql,[]))
    transition = MagicMock(return_value=([],1,1,False))
    monkeypatch.setattr(service,"_commit_closure_transition",transition)
    report = {"mois_cloture":"2026-01","mois_suivant":"2026-02","checks":[]}
    result = service.run_piste_a_month_closure(settings,"2026-01","2026-02",expected=object(),precheck_report=report)
    assert result.tabsme_rows_closed == 1
    assert transition.call_args.kwargs["audit_payload"]["precheck_report"] == report
    assert transition.call_args.kwargs["audit_payload"]["mysql_archive"] == str(sql)


def test_zip_handoff_selects_exact_file_without_restore(app,tmp_path,monkeypatch):
    from sme_gend.ui.views import archives_view as ui
    monkeypatch.setattr(ui,"session_has_permission",lambda *a:True)
    monkeypatch.setattr(ui,"require_permission",lambda *a:True)
    path = tmp_path/"backup.zip"
    path.write_bytes(b"synthetic")
    view = ui.ArchivesView()
    view._selected_path = lambda:path
    view._go_page = MagicMock()
    view._backup_restore_view = MagicMock()
    view._open_selected()
    view._backup_restore_view.select_archive_for_restore.assert_called_once_with(path)
    view._backup_restore_view._on_restore_partial.assert_not_called()
    view._backup_restore_view._on_restore_full.assert_not_called()
    view.close()


def test_resolving_a_done_task_does_not_rewrite_its_author_or_date(task_store):
    from sme_gend.services.workflow_task_service import WorkflowTaskRow
    from sme_gend.repositories.mysql_connection import mysql_database_key
    obj,cursor,audit = task_store
    row = WorkflowTaskRow(7,"2026-01","manual","done","normal","Contrôle","Détail",None,None,
                          write_token="workflow_task:7:"+"a"*64, _write_database=mysql_database_key(obj._settings))
    cursor.fetchone.return_value = ("done","a"*64)
    assert obj.resolve_task(7,expected=row) is False
    assert not any(c.args[0].lstrip().startswith("UPDATE") for c in cursor.execute.call_args_list)
    audit.assert_not_called()


def test_preview_uses_central_working_month_instead_of_consulted_month(app,monkeypatch):
    from sme_gend.ui.views import month_pilotage_view as ui
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission",lambda *a:True)
    monkeypatch.setattr("sme_gend.services.sme_workspace_service.SmeWorkspaceService",lambda settings:
        SimpleNamespace(load_active_month_snapshot=lambda:SimpleNamespace(active_month="2026-01")))
    monkeypatch.setattr(ui,"run_with_progress",lambda parent,title,text,work:work())
    runner = MagicMock(return_value=pre.PreClosureReport("2026-01","2026-02",()))
    monkeypatch.setattr(ui,"run_pre_closure_checks",runner)
    dialog = MagicMock()
    monkeypatch.setattr("sme_gend.ui.pre_closure_report_dialog.PreClosureReportDialog",dialog)
    view = ui.MonthPilotageView(settings,MagicMock())
    view._run_preclose_preview()
    runner.assert_called_once_with(settings,"2026-01","2026-02")
    dialog.return_value.exec.assert_called_once()
    view.close()
