"""The session guard must cover progress, result acknowledgment, and shutdown."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from sme_gend.ui.views import backup_restore_view as ui


@pytest.fixture
def guarded_view(monkeypatch):
    events = []
    guard = SimpleNamespace(exit_after_operation=False)
    state = SimpleNamespace(active=False)

    @contextmanager
    def defer():
        events.append("guard_enter")
        state.active = True
        try:
            yield guard
        finally:
            events.append(("guard_exit", guard.exit_after_operation))
            state.active = False

    window = SimpleNamespace(defer_session_lock_for_operation=defer)
    settings = SimpleNamespace(mysql_host="synthetic.invalid", mysql_port=33329, mysql_database="test_only")
    view = SimpleNamespace(_settings=settings, window=lambda: window, _append_log=Mock())
    monkeypatch.setattr(ui, "session_can_perform_action", lambda key: True)
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_admin_role", lambda: True)
    return SimpleNamespace(view=view, window=window, guard=guard, state=state, events=events)


def configure_reset(monkeypatch, harness):
    target = "synthetic.invalid:33329/test_only"
    options = object()
    dialog = SimpleNamespace(
        exec=lambda: QDialog.DialogCode.Accepted,
        options=lambda: options,
        username=SimpleNamespace(text=lambda: "SYNTHETIC"),
        password=SimpleNamespace(text=lambda: "SYNTHETIC_PASSWORD"),
    )
    monkeypatch.setattr("sme_gend.ui.application_reset_dialog.ApplicationResetDialog", lambda *args: dialog)
    monkeypatch.setattr("sme_gend.services.application_reset.reset_target", lambda settings: target)
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: (target, True))
    return options, target


def test_successful_reset_keeps_guard_through_worker_result_and_only_then_quits(monkeypatch, guarded_view):
    harness = guarded_view
    options, target = configure_reset(monkeypatch, harness)
    result = {"backup": "synthetic_backup.zip", "recovery": "synthetic_recovery", "warnings": []}

    def reset(settings, supplied_options, **kwargs):
        assert harness.state.active
        assert settings is harness.view._settings and supplied_options is options
        assert kwargs == {"confirmation": target, "new_username": "SYNTHETIC", "new_password": "SYNTHETIC_PASSWORD"}
        harness.events.append("service")
        return result

    def worker(parent, title, text, work):
        assert harness.state.active and not harness.guard.exit_after_operation
        harness.events.append("worker")
        return work()

    def acknowledge(parent, title, text):
        assert harness.state.active and not harness.guard.exit_after_operation
        assert title == "Réinitialisation terminée" and result["backup"] in text
        harness.events.append("result_acknowledged")

    def quit_app():
        assert harness.state.active and harness.guard.exit_after_operation
        harness.events.append("quit")

    monkeypatch.setattr("sme_gend.services.application_reset.reset_application", reset)
    monkeypatch.setattr(ui, "run_with_progress", worker)
    monkeypatch.setattr(QMessageBox, "information", acknowledge)
    monkeypatch.setattr(QApplication, "quit", quit_app)
    ui.BackupRestoreView._on_reset_application(harness.view)
    assert harness.events == ["guard_enter", "worker", "service", "result_acknowledged", "quit", ("guard_exit", True)]
    assert not harness.state.active


def test_reset_worker_returning_none_never_reports_success_or_quits(monkeypatch, guarded_view):
    harness = guarded_view
    configure_reset(monkeypatch, harness)
    service = Mock()

    def worker(*args):
        assert harness.state.active
        harness.events.append("worker_none")
        return None

    info, quit_app = Mock(), Mock()
    monkeypatch.setattr("sme_gend.services.application_reset.reset_application", service)
    monkeypatch.setattr(ui, "run_with_progress", worker)
    monkeypatch.setattr(QMessageBox, "information", info)
    monkeypatch.setattr(QApplication, "quit", quit_app)
    ui.BackupRestoreView._on_reset_application(harness.view)
    assert harness.events == ["guard_enter", "worker_none", ("guard_exit", False)]
    assert not harness.guard.exit_after_operation
    info.assert_not_called()
    quit_app.assert_not_called()
    service.assert_not_called()


def test_reset_worker_exception_keeps_error_inside_guard_and_never_quits(monkeypatch, guarded_view):
    harness = guarded_view
    configure_reset(monkeypatch, harness)

    def worker(*args):
        assert harness.state.active
        harness.events.append("worker_failed")
        raise ValueError("synthetic failure")

    def error(parent, title, detail):
        assert harness.state.active and not harness.guard.exit_after_operation
        assert title == "Réinitialisation interrompue" and detail == "synthetic failure"
        harness.events.append("error_acknowledged")

    info, quit_app = Mock(), Mock()
    monkeypatch.setattr(ui, "run_with_progress", worker)
    monkeypatch.setattr(QMessageBox, "critical", error)
    monkeypatch.setattr(QMessageBox, "information", info)
    monkeypatch.setattr(QApplication, "quit", quit_app)
    ui.BackupRestoreView._on_reset_application(harness.view)
    assert harness.events == ["guard_enter", "worker_failed", "error_acknowledged", ("guard_exit", False)]
    info.assert_not_called()
    quit_app.assert_not_called()


@pytest.mark.parametrize("success", [False, True])
def test_restore_keeps_guard_through_result_and_reload_then_rechecks_session(monkeypatch, guarded_view, success):
    harness = guarded_view

    def work():
        assert harness.state.active
        harness.events.append("service")
        return {"synthetic": True} if success else None

    def worker(parent, title, text, fn):
        assert harness.state.active
        harness.events.append("worker")
        return fn()

    def acknowledge(*args):
        assert harness.state.active and not harness.guard.exit_after_operation
        harness.events.append("result_acknowledged")

    def reload(**kwargs):
        assert harness.state.active and kwargs == {"show_success_dialog": False}
        harness.events.append("reload")

    def refresh():
        assert harness.state.active
        harness.events.append("refresh")

    harness.window.reload_runtime_configuration = reload
    harness.window.refresh_for_month_change = refresh
    quit_app = Mock()
    monkeypatch.setattr(ui, "run_with_progress", worker)
    monkeypatch.setattr(QMessageBox, "information", acknowledge)
    monkeypatch.setattr(QApplication, "quit", quit_app)
    ui.BackupRestoreView._finish_restore(harness.view, work, "synthetic.zip", True, True)
    expected = ["guard_enter", "worker", "service"]
    if success:
        expected += ["result_acknowledged", "reload", "refresh"]
    assert harness.events == expected + [("guard_exit", False)]
    assert not harness.guard.exit_after_operation and not harness.state.active
    quit_app.assert_not_called()
