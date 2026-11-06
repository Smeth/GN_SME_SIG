from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sme_gend.config.auth_config import AuthConfig, AuthUser
from sme_gend.services.auth_service import AuthService, AuthSession


def auth_session():
    auth = AuthService.__new__(AuthService)
    auth._config = AuthConfig(enabled=True, users=[AuthUser('SYNTHETIC', '', '', role='operateur')])
    auth._enforced = True
    auth._settings = lambda: object()
    auth.reload_config = lambda: None
    auth._session = AuthSession('SYNTHETIC', 'Synthetic', 'operateur',
                                datetime.now(timezone.utc) + timedelta(hours=1), session_token='TOKEN')
    return auth


@pytest.mark.parametrize('failure', ['config', 'heartbeat'])
def test_outage_blocks_permissions_but_preserves_token_for_verified_recovery(monkeypatch, failure):
    auth = auth_session()
    original = auth._session
    def unavailable(*a, **kw):
        raise RuntimeError('SYNTHETIC OUTAGE')
    heartbeat = Mock(return_value=True)
    monkeypatch.setattr('sme_gend.services.auth_session_registry.heartbeat_session', heartbeat)
    if failure == 'config':
        auth.reload_config = unavailable
    else:
        heartbeat.side_effect = unavailable
    with pytest.raises(RuntimeError, match='OUTAGE'):
        auth.sync_session_from_config()
    assert auth._session is original
    assert auth.current_session() is None
    assert not auth.session_has_permission('effectifs.edition')
    assert auth.record_activity() is None
    with pytest.raises(PermissionError, match='attente'):
        auth.ensure_active_session_registered()
    auth.reload_config = lambda: None
    heartbeat.side_effect = None
    assert auth.sync_session_from_config() in ('ok', 'updated')
    assert auth.current_session().session_token == original.session_token
    assert auth.current_session().expires_at == original.expires_at


@pytest.mark.parametrize('reason', ['revoked', 'disabled', 'revision', 'expired'])
def test_outage_does_not_bypass_revocation_or_expiration(monkeypatch, reason):
    auth = auth_session()
    auth._session_verification_pending = True
    heartbeat = Mock(return_value=True)
    monkeypatch.setattr('sme_gend.services.auth_session_registry.heartbeat_session', heartbeat)
    if reason == 'revoked': heartbeat.return_value = False
    elif reason == 'disabled': auth._config.users[0].disabled = True
    elif reason == 'revision': auth._config.users[0].session_rev = 1
    else:
        from dataclasses import replace
        auth._session = replace(auth._session, expires_at=datetime.now(timezone.utc)-timedelta(seconds=1))
    assert auth.sync_session_from_config() == 'invalid'
    assert auth._session is None


def test_shell_retries_without_logout_or_discarding_drafts(monkeypatch):
    from sme_gend.ui.shell_window import ShellWindow
    from PySide6.QtCore import QEvent
    auth = Mock()
    original = object()
    auth.is_enforced.return_value = True
    auth.current_session.return_value = original
    auth.sync_session_from_config.side_effect = [RuntimeError('OUTAGE'), 'ok']
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: auth)
    view = SimpleNamespace(_auth_session=original, _session_lock_active=False,
        _session_verification_pending=False, _session_lock_overlay=Mock(), _lock_session=Mock(),
        statusBar=Mock(), _SESSION_ACTIVITY=ShellWindow._SESSION_ACTIVITY)
    view._suspend_session_verification = lambda: ShellWindow._suspend_session_verification(view)
    ShellWindow._check_session_expiry(view)
    assert view._session_verification_pending and view._auth_session is original
    auth.logout.assert_not_called()
    view._lock_session.assert_not_called()
    assert ShellWindow.eventFilter(view, object(), QEvent(QEvent.Type.KeyPress))
    ShellWindow._check_session_expiry(view)
    assert not view._session_verification_pending
    view._session_lock_overlay.hide.assert_called_once()
    assert view._auth_session is original
    auth.logout.assert_not_called()


def test_strict_auth_read_does_not_replace_config_with_empty_accounts(monkeypatch):
    from sme_gend.config.auth_config import load_auth_config
    from sme_gend.services import app_config_store
    def unavailable(*a, **kw):
        raise RuntimeError('DB DOWN')
    monkeypatch.setattr(app_config_store, '_repo', unavailable)
    with pytest.raises(RuntimeError, match='indisponible'):
        load_auth_config(strict=True)


def test_explicit_logout_clears_session_and_opens_login_without_closing(monkeypatch):
    from sme_gend.ui.shell_window import ShellWindow
    auth = Mock()
    auth.is_enforced.return_value = True
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: auth)
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_yes_no', lambda *a, **kw: True)
    scheduled = []
    monkeypatch.setattr('sme_gend.ui.shell_window.QTimer.singleShot', lambda delay, fn: scheduled.append(fn))
    view = SimpleNamespace(_auth_session=object(), _session_lock_active=False,
        _session_verification_pending=False, _session_guard_timer=Mock(),
        _session_lock_overlay=Mock(), _prompt_relogin=Mock(), close=Mock())
    view._begin_relogin_guard = lambda **kw: ShellWindow._begin_relogin_guard(view, **kw)
    ShellWindow._logout_user(view)
    auth.logout.assert_called_once()
    assert view._auth_session is None and view._session_lock_active
    view._session_guard_timer.stop.assert_called_once()
    view._session_lock_overlay.show.assert_called_once()
    assert scheduled == [view._prompt_relogin]
    scheduled[0]()
    view._prompt_relogin.assert_called_once()
    view.close.assert_not_called()


def test_cancel_logout_keeps_session(monkeypatch):
    from sme_gend.ui.shell_window import ShellWindow
    auth = Mock()
    auth.is_enforced.return_value = True
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: auth)
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_yes_no', lambda *a, **kw: False)
    view = SimpleNamespace(_session_lock_active=False, _begin_relogin_guard=Mock())
    ShellWindow._logout_user(view)
    view._begin_relogin_guard.assert_not_called()
    auth.logout.assert_not_called()
