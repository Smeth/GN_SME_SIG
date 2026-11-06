"""Choix du mois, aperçu et confirmation sur des données synthétiques."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication
from test_month_transfer import source, live
from sme_gend.config.settings import Settings
from sme_gend.services import month_transfer as service


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app,source,live,monkeypatch):
    from sme_gend.ui import month_transfer_dialog as ui
    monkeypatch.setattr(ui,"run_with_progress",lambda parent,title,text,work:work())
    monkeypatch.setattr("sme_gend.repositories.sme_repository.SmeRepository",lambda settings:SimpleNamespace(fetch_all=lambda:[SimpleNamespace(mois="2026-01"),SimpleNamespace(mois="2026-09")]))
    monkeypatch.setattr(ui.QMessageBox,"information",lambda *a:None)
    settings = Settings(database_path=Path("unused"))
    view = ui.MonthTransferDialog(None,settings)
    view._source = source
    view._mode.setCurrentIndex(2)
    view._month.setCurrentIndex(view._month.findData("2026-01"))
    plan = service.build_plan(source,live,"2026-01",("test",))
    monkeypatch.setattr(service,"preview_month",lambda *a:plan)
    yield view,ui,plan
    view.close()


def test_apply_is_disabled_until_control_and_selection_change_invalidates_plan(dialog):
    view,ui,plan = dialog
    assert not view._execute.isEnabled()
    view._check()
    assert view._execute.isEnabled() and "2026-01" in view._detail.toPlainText()
    view._month.setCurrentIndex(view._month.findData("2026-09"))
    assert view._plan is None and not view._execute.isEnabled()


def test_cancel_confirmation_never_calls_restore(dialog,monkeypatch):
    view,ui,plan = dialog
    view._check()
    monkeypatch.setattr(ui,"confirm_destructive_with_ack",lambda *a,**kw:False)
    apply = MagicMock()
    monkeypatch.setattr(service,"apply_month",apply)
    view._run()
    apply.assert_not_called()


def test_context_change_during_confirmation_prevents_restore(dialog,monkeypatch):
    view,ui,plan = dialog
    view._check()
    def changed(*a,**kw):
        view._month.setCurrentIndex(view._month.findData("2026-09"))
        return True
    monkeypatch.setattr(ui,"confirm_destructive_with_ack",changed)
    apply = MagicMock()
    monkeypatch.setattr(service,"apply_month",apply)
    view._run()
    apply.assert_not_called()


def test_confirmed_restore_transmits_only_displayed_plan(dialog,monkeypatch):
    view,ui,plan = dialog
    view._check()
    monkeypatch.setattr(ui,"confirm_destructive_with_ack",lambda *a,**kw:True)
    apply = MagicMock(return_value={"backup":"synthetic.zip"})
    monkeypatch.setattr(service,"apply_month",apply)
    view._run()
    apply.assert_called_once_with(view._settings,plan)
    assert view._plan is None


def test_identity_conflicts_disable_restore_and_remain_readable(dialog,source,live,monkeypatch):
    view,ui,_ = dialog
    live["agent"]["rows"][0]["noms"] = "UNE AUTRE IDENTITE"
    plan = service.build_plan(source,live,"2026-01",("test",))
    monkeypatch.setattr(service,"preview_month",lambda *a:plan)
    view._check()
    assert not view._execute.isEnabled()
    assert "données partagées différentes" in view._detail.toPlainText()


def test_import_mode_refuses_existing_period(dialog,live,monkeypatch):
    view,ui,plan = dialog
    monkeypatch.setattr(service,"read_database",lambda *a:live)
    view._mode.setCurrentIndex(1)
    view._month.setCurrentIndex(view._month.findData("2026-01"))
    view._check()
    assert not view._execute.isEnabled()
    assert "existe déjà" in view._detail.toPlainText()


def test_failed_source_load_clears_previous_source_and_plan(dialog,monkeypatch):
    view,ui,plan = dialog
    view._check()
    monkeypatch.setattr(service,"load_source",lambda *a:None)
    view._load_path(Path("synthetic.sql"))
    assert view._source is None and view._plan is None
    assert view._month.count() == 0 and not view._execute.isEnabled()


def test_export_cancellation_never_creates_file(dialog,monkeypatch):
    view,ui,plan = dialog
    view._mode.setCurrentIndex(0)
    monkeypatch.setattr(ui.QFileDialog,"getSaveFileName",lambda *a:("",""))
    export = MagicMock()
    monkeypatch.setattr(service,"export_month",export)
    view._run()
    export.assert_not_called()


def test_archive_actions_open_the_month_dialog_with_selected_sql(app,monkeypatch,tmp_path):
    from sme_gend.ui.views import archives_view as ui
    monkeypatch.setattr(ui,"require_permission",lambda *a:True)
    path = tmp_path/"global.sql"
    path.write_text("SYNTHETIC")
    view = ui.ArchivesView()
    view._selected_path = lambda:path
    open_month = MagicMock()
    monkeypatch.setattr(view,"_monthly_transfer",open_month)
    view._open_selected()
    open_month.assert_called_once_with(initial_source=path)
    view.close()
