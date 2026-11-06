from datetime import datetime, timedelta, timezone
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.services import auth_session_registry as registry
from sme_gend.config.auth_config import AuthConfig, AuthUser
from sme_gend.services.auth_service import AuthService, AuthSession
from sme_gend.repositories.mysql_connection import MySQLConnection


def active_auth(journal):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, _ = journal
    cfg = AuthConfig(enabled=True, users=[AuthUser('SYNTHETIC', '', '', role='operateur')])
    AppConfigRepository(settings).set_json('auth_config', cfg.to_dict())
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    token, error = registry.try_register_session(settings, cfg, username='SYNTHETIC', session_rev=0,
        client_instance_id='SYNTHETIC', machine_label='SYNTHETIC', expires_at=expiry)
    assert token and not error
    auth = AuthService.__new__(AuthService)
    auth._config = cfg; auth._enforced = True; auth._settings = lambda: settings
    auth._session = AuthSession('SYNTHETIC', 'SYNTHETIC', 'operateur', expiry, session_token=token)
    return settings, auth, token


@pytest.mark.parametrize('revoke', [False, True])
def test_config_outage_recovers_only_if_registered_token_remains_valid(journal, monkeypatch, revoke):
    from sme_gend.services import app_config_store
    settings, auth, token = active_auth(journal)
    assert auth.sync_session_from_config() == 'ok'
    with monkeypatch.context() as patch:
        def unavailable(*a, **kw): raise RuntimeError('SYNTHETIC OUTAGE')
        patch.setattr(app_config_store, '_repo', unavailable)
        with pytest.raises(RuntimeError, match='indisponible'):
            auth.sync_session_from_config()
    assert auth.current_session() is None and auth._session.session_token == token
    if revoke: registry.unregister_session(settings, token)
    assert auth.sync_session_from_config() == ('invalid' if revoke else 'ok')
    assert (auth.current_session() is None) == revoke


def test_stale_heartbeat_does_not_shorten_active_expiration(journal):
    settings, auth, token = active_auth(journal)
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT expires_at FROM auth_active_session WHERE session_token=%s', (token,))
            expected = cur.fetchone()[0]
    assert registry.heartbeat_session(settings, session_token=token, username='SYNTHETIC', session_rev=0,
        expires_at=datetime.now(timezone.utc)+timedelta(minutes=5))
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT expires_at FROM auth_active_session WHERE session_token=%s', (token,))
            assert cur.fetchone()[0] == expected


def test_heartbeat_cannot_revive_expired_registration(journal):
    settings, auth, token = active_auth(journal)
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('UPDATE auth_active_session SET expires_at=%s WHERE session_token=%s',
                        (datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=1), token))
    assert not registry.heartbeat_session(settings, session_token=token, username='SYNTHETIC', session_rev=0,
                                          expires_at=auth._session.expires_at)
