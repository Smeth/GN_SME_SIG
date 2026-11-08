"""Destructive-operation results stay readable without retaining session access."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sme_gend.ui.shell_window import ShellWindow


def shell_stub(monkeypatch, *, checks=("invalid",)):
    auth = Mock()
    auth.is_enforced.return_value = True
    auth.current_session.return_value = None
    auth.sync_session_from_config.side_effect = list(checks)
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    scheduled = []
    monkeypatch.setattr("sme_gend.ui.shell_window.QTimer.singleShot", lambda delay, fn: scheduled.append(fn))
    shell = SimpleNamespace(
        _auth_session=SimpleNamespace(username="SYNTHETIC"),
        _session_lock_active=False,
        _session_verification_pending=False,
        _session_operation_depth=0,
        _session_operation_exit_pending=False,
        _session_operation_exiting=False,
        _session_lock_presentation_pending=False,
        _session_lock_overlay=Mock(),
        _session_guard_timer=Mock(),
        _prompt_relogin=Mock(),
        statusBar=Mock(),
        _SESSION_ACTIVITY=ShellWindow._SESSION_ACTIVITY,
    )
    shell._lock_session = lambda **kw: ShellWindow._lock_session(shell, **kw)
    shell._begin_relogin_guard = lambda **kw: ShellWindow._begin_relogin_guard(shell, **kw)
    shell._suspend_session_verification = lambda: ShellWindow._suspend_session_verification(shell)
    return shell, auth, scheduled


def test_revocation_removes_session_before_result_but_login_waits(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch)
    with ShellWindow.defer_session_lock_for_operation(shell):
        ShellWindow._check_session_expiry(shell)
        assert shell._session_lock_active and shell._auth_session is None
        auth.logout.assert_called_once()
        shell._session_guard_timer.stop.assert_called_once()
        shell._session_lock_overlay.show.assert_not_called()
        assert scheduled == []
        assert ShellWindow._shared_refresh_blocked(shell)
    shell._session_lock_overlay.show.assert_called_once()
    assert scheduled == [shell._prompt_relogin]


def test_short_restore_checks_session_after_result_even_before_heartbeat(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch)
    with ShellWindow.defer_session_lock_for_operation(shell):
        auth.sync_session_from_config.assert_not_called()
        assert scheduled == []
    auth.sync_session_from_config.assert_called_once()
    assert shell._auth_session is None and scheduled == [shell._prompt_relogin]


def test_actual_auth_service_denies_actions_while_revoked_login_is_deferred(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    from sme_gend.services.auth_service import AuthService, AuthSession

    shell, unused_auth, scheduled = shell_stub(monkeypatch)
    auth = AuthService.__new__(AuthService)
    auth._config = AuthConfig(enabled=True, users=[AuthUser("SYNTHETIC", "", "", role="administrateur")])
    auth._enforced = True
    auth._settings = lambda: object()
    auth.reload_config = lambda: None
    auth._session = AuthSession(
        "SYNTHETIC", "Synthetic", "administrateur",
        datetime.now(timezone.utc) + timedelta(hours=1), session_token="SYNTHETIC_TOKEN",
    )
    shell._auth_session = auth._session
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    monkeypatch.setattr("sme_gend.services.auth_session_registry.heartbeat_session", lambda *a, **kw: False)
    with ShellWindow.defer_session_lock_for_operation(shell):
        ShellWindow._check_session_expiry(shell)
        assert auth.current_session() is None
        assert not auth.session_has_permission("system.restore")
        assert not auth.session_has_permission("effectifs.edition")
        shell._session_lock_overlay.show.assert_not_called()
        assert scheduled == []
    assert scheduled == [shell._prompt_relogin]


def test_failed_operation_keeps_its_exception_and_then_presents_lock(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch)
    error = ValueError("synthetic operation failure")
    with pytest.raises(ValueError) as raised:
        with ShellWindow.defer_session_lock_for_operation(shell):
            ShellWindow._check_session_expiry(shell)
            raise error
    assert raised.value is error
    assert scheduled == [shell._prompt_relogin]


def test_successful_reset_can_quit_after_result_without_opening_login(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch)
    with ShellWindow.defer_session_lock_for_operation(shell) as guard:
        ShellWindow._check_session_expiry(shell)
        # The caller sets this only after the result has been acknowledged.
        guard.exit_after_operation = True
    assert shell._session_lock_active and shell._auth_session is None
    shell._session_lock_overlay.show.assert_not_called()
    assert scheduled == []
    assert shell._session_operation_exiting
    assert ShellWindow._shared_refresh_blocked(shell)


def test_nested_operation_does_not_present_login_between_steps(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch)
    with ShellWindow.defer_session_lock_for_operation(shell):
        with ShellWindow.defer_session_lock_for_operation(shell):
            ShellWindow._check_session_expiry(shell)
        assert shell._session_operation_depth == 1 and scheduled == []
    assert shell._session_operation_depth == 0
    assert scheduled == [shell._prompt_relogin]


@pytest.mark.parametrize("recovers", [False, True])
def test_connection_check_outage_waits_for_result_and_keeps_retry_policy(monkeypatch, recovers):
    shell, auth, scheduled = shell_stub(
        monkeypatch, checks=(RuntimeError("synthetic outage"), "ok" if recovers else RuntimeError("still down"))
    )
    original = shell._auth_session
    if recovers:
        auth.current_session.return_value = original
    with ShellWindow.defer_session_lock_for_operation(shell):
        ShellWindow._check_session_expiry(shell)
        assert shell._session_verification_pending
        shell._session_lock_overlay.show.assert_not_called()
    assert shell._auth_session is original
    assert shell._session_verification_pending is not recovers
    assert scheduled == []
    auth.logout.assert_not_called()
    if recovers:
        shell._session_lock_overlay.show.assert_not_called()
    else:
        shell._session_lock_overlay.show.assert_called_once()


def test_queued_relogin_cannot_cover_a_new_operation(monkeypatch):
    shell, auth, scheduled = shell_stub(monkeypatch, checks=("ok",))
    with ShellWindow.defer_session_lock_for_operation(shell) as guard:
        ShellWindow._prompt_relogin(shell)
        assert shell._session_lock_presentation_pending
        guard.exit_after_operation = True
    assert scheduled == []


def test_operation_blocks_shell_input_but_allows_modal_result_buttons(monkeypatch):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton

    app = QApplication.instance() or QApplication([])
    # Initialize just the Qt base: these checks must not build pages or access MySQL.
    shell = ShellWindow.__new__(ShellWindow)
    QMainWindow.__init__(shell)
    shell._session_operation_depth = 1
    shell._session_lock_active = True
    modal = QDialog(shell)
    modal.setWindowModality(Qt.WindowModality.ApplicationModal)
    button = QPushButton("Result", modal)
    monkeypatch.setattr(QApplication, "activeModalWidget", lambda: modal)
    key = QEvent(QEvent.Type.KeyPress)
    assert ShellWindow.eventFilter(shell, shell, key)
    assert not ShellWindow.eventFilter(shell, button, key)
    # Also remain blocked if no modal is currently shown between two phases.
    monkeypatch.setattr(QApplication, "activeModalWidget", lambda: None)
    assert ShellWindow.eventFilter(shell, shell, key)
    shell._session_operation_depth = 0
    shell._session_operation_exiting = True
    assert ShellWindow.eventFilter(shell, shell, key)
    modal.deleteLater()
    shell.deleteLater()
    app.processEvents()


def test_real_modal_result_still_receives_mouse_and_enter_with_guard_installed():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton, QVBoxLayout

    app = QApplication.instance() or QApplication([])
    shell = ShellWindow.__new__(ShellWindow)
    QMainWindow.__init__(shell)
    shell._session_operation_depth = 1
    shell._session_lock_active = True
    dialog = QDialog(shell)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    button = QPushButton("Acknowledge", dialog)
    QVBoxLayout(dialog).addWidget(button)
    clicks = []
    button.clicked.connect(lambda: clicks.append(True))
    app.installEventFilter(shell)
    try:
        dialog.show()
        app.processEvents()
        QTest.mouseClick(
            dialog.windowHandle(), Qt.MouseButton.LeftButton,
            pos=button.mapTo(dialog, button.rect().center()),
        )
        QTest.keyClick(button, Qt.Key.Key_Return)
        assert clicks == [True, True]
    finally:
        app.removeEventFilter(shell)
        dialog.close()
        dialog.deleteLater()
        shell.deleteLater()
        app.processEvents()
