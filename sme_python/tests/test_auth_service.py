"""Tests authentification locale."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sme_gend.services.auth_service import AuthService, hash_new_password, verify_password
from sme_gend.config.auth_config import AuthUser, DEFAULT_ADMIN_PASSWORD


def test_hash_and_verify():
    h, s = hash_new_password("secret123")
    u = AuthUser(username="u1", password_hash=h, salt=s)
    assert verify_password("secret123", u)
    assert not verify_password("wrong", u)


def _wire_memory_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    store: dict[str, dict] = {}

    def _load(_settings, key, **kwargs):
        return store.get(key)

    def _save(_settings, key, payload, **kwargs):
        store[key] = payload

    monkeypatch.setattr(
        "sme_gend.services.app_config_store.load_config_dict",
        _load,
    )
    monkeypatch.setattr(
        "sme_gend.services.app_config_store.save_config_dict",
        _save,
    )
    return store


def test_auth_flow(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.reload_config()

    assert auth.authenticate("admin", "adminpass") is not None
    assert auth.authenticate("admin", "bad") is None


def test_lockout_after_max_failed_attempts(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("recovery", "recovery1", role="super_admin")
    auth.set_enabled(True)
    auth.reload_config()

    for _ in range(5):
        assert auth.authenticate("admin", "wrong") is None

    # 6e tentative : verrouillé, même avec le bon mot de passe.
    assert auth.authenticate("admin", "adminpass") is None
    assert "verrouill" in (auth.last_login_error or "").casefold()

    # Un changement de mot de passe (admin) débloque le compte.
    assert auth.authenticate("recovery", "recovery1") is not None
    auth.change_password("admin", "newpass1")
    assert auth.authenticate("admin", "newpass1") is not None


def test_change_own_password_success_and_errors(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.reload_config()
    assert auth.authenticate("admin", "adminpass") is not None

    auth.change_own_password("adminpass", "nouveauMdp1")
    assert auth.authenticate("admin", "nouveauMdp1") is not None

    auth.authenticate("admin", "nouveauMdp1")
    with pytest.raises(ValueError, match="incorrect"):
        auth.change_own_password("wrong", "autreMdp12")
    with pytest.raises(ValueError, match="différent"):
        auth.change_own_password("nouveauMdp1", "nouveauMdp1")


def test_session_sync_after_role_change(monkeypatch):
    from sme_gend.config.auth_config import save_auth_config

    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("op1", "operator1", role="operateur")
    auth.set_enabled(True)
    auth.reload_config()
    session = auth.authenticate("op1", "operator1")
    assert session is not None
    assert auth.session_has_permission("effectifs.edition") is True

    auth.reload_config()
    user = auth.get_user("op1")
    assert user is not None
    user.role = "lecteur"
    user.session_rev = int(user.session_rev or 0) + 1
    save_auth_config(auth.config)
    assert auth.sync_session_from_config() == "invalid"
    assert auth.current_session() is None
    assert auth.session_has_permission("effectifs.edition") is False


def test_disabled_user_cannot_login(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("op1", "operator1", role="operateur")
    auth.set_enabled(True)
    auth.reload_config()
    auth.authenticate("admin", "adminpass")
    auth.update_user("op1", disabled=True)
    auth.logout()
    assert auth.authenticate("op1", "operator1") is None
    assert "désactivé" in (auth.last_login_error or "").casefold()


def test_rename_user(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("op1", "operator1", role="operateur")
    auth.rename_user("op1", "op2")
    assert auth.get_user("op1") is None
    assert auth.get_user("op2") is not None


def test_auth_lockout_settings(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    assert auth.authenticate("admin", "adminpass") is not None
    auth.save_auth_settings(
        enabled=True,
        session_timeout_minutes=480,
        max_failed_attempts=3,
        lockout_minutes=10,
    )
    auth.reload_config()
    assert auth.config.max_failed_attempts == 3
    assert auth.config.lockout_minutes == 10


def test_must_change_password_flag_on_default_password(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin(DEFAULT_ADMIN_PASSWORD)
    auth.set_enabled(True)
    auth.reload_config()

    session = auth.authenticate("admin", DEFAULT_ADMIN_PASSWORD)
    assert session is not None
    assert session.must_change_password is True

    auth.change_password("admin", "unNouveauMdp1")
    session2 = auth.authenticate("admin", "unNouveauMdp1")
    assert session2 is not None
    assert session2.must_change_password is False


def test_session_can_perform_action_denies_unknown_action(monkeypatch):
    from sme_gend.services.workflow_permissions import session_can_perform_action

    monkeypatch.setattr(
        "sme_gend.services.auth_context.get_app_auth",
        lambda: object(),
    )
    assert session_can_perform_action("totally.unregistered.action") is False


def test_session_expiry_helpers(monkeypatch):
    _wire_memory_store(monkeypatch)

    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.reload_config()

    session = auth.authenticate("admin", "adminpass")
    assert session is not None
    assert auth.seconds_until_expiry() is not None
    assert auth.seconds_until_expiry() > 0
    assert not auth.session_is_expired()

    auth._session = auth._session.__class__(
        username=session.username,
        display_name=session.display_name,
        role=session.role,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        must_change_password=False,
    )
    assert auth.session_is_expired()
    assert auth.seconds_until_expiry() == 0
    assert auth.invalidate_expired_session()
    assert auth.current_session() is None


def test_user_activity_renews_session_expiry(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.reload_config()
    session = auth.authenticate("admin", "adminpass")
    assert session is not None

    activity_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    renewed = auth.record_activity(at=activity_at)

    assert renewed is not None
    assert renewed.expires_at == activity_at + timedelta(
        minutes=auth.config.session_timeout_minutes
    )


def test_logout_clears_local_session_when_registry_fails(monkeypatch, caplog):
    from sme_gend.services.auth_service import AuthSession

    auth = AuthService()
    auth._session = AuthSession(
        username="user1",
        display_name="User 1",
        role="operateur",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        session_token="token",
    )
    monkeypatch.setattr(
        "sme_gend.services.auth_session_registry.unregister_session",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(auth, "_settings", lambda: object())
    monkeypatch.setattr(auth, "_audit", lambda **_kwargs: None)

    with caplog.at_level("ERROR"):
        auth.logout()

    assert auth._session is None
    assert "Impossible de supprimer du registre MySQL" in caplog.text


def test_is_enabled_does_not_reload_from_store(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.reload_config()
    auth._config.enabled = False
    assert auth.is_enabled() is False


def test_password_min_length(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    with pytest.raises(ValueError, match="8"):
        auth.add_user("bob", "short")


def test_cannot_remove_last_super_admin(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("op1", "operateur1", role="operateur")
    with pytest.raises(ValueError, match="dernier super"):
        auth.update_user_role("admin", "operateur")
    with pytest.raises(ValueError, match="dernier super"):
        auth.remove_user("admin")


def test_cannot_delete_own_session_account(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("op1", "operateur1", role="operateur")
    auth.set_enabled(True)
    assert auth.authenticate("admin", "adminpass") is not None
    with pytest.raises(ValueError, match="session en cours"):
        auth.remove_user("admin")


def test_admin_cannot_assign_super_admin(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("chef", "chefpass1", role="admin")
    auth.set_enabled(True)
    assert auth.authenticate("chef", "chefpass1") is not None
    with pytest.raises(ValueError, match="super administrateur"):
        auth.add_user("root2", "rootpass1", role="super_admin")
    with pytest.raises(ValueError, match="super administrateur"):
        auth.update_user_role("chef", "super_admin")


def test_unlock_user_clears_lockout(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.add_user("recovery", "recovery1", role="super_admin")
    auth.set_enabled(True)
    for _ in range(5):
        assert auth.authenticate("admin", "wrong") is None
    assert auth.authenticate("recovery", "recovery1") is not None
    auth.unlock_user("admin")
    assert auth.authenticate("admin", "adminpass") is not None


def test_lock_user_blocks_login_until_unlock(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    auth.add_user("op1", "operator1", role="operateur")
    auth.lock_user("op1")
    assert auth.authenticate("op1", "operator1") is None
    auth.unlock_user("op1")
    assert auth.authenticate("op1", "operator1") is not None


def test_lock_user_cannot_lock_self(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    auth.set_enabled(True)
    auth.authenticate("admin", "adminpass")
    with pytest.raises(ValueError, match="propre compte"):
        auth.lock_user("admin")


def test_session_has_admin_role_when_not_enforced(monkeypatch):
    from sme_gend.services.auth_guard import session_has_admin_role, session_is_super_admin

    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    assert session_has_admin_role() is True
    assert session_is_super_admin() is True
    auth.set_enabled(True)
    auth.mark_enforced()
    assert session_has_admin_role() is False
    assert auth.authenticate("admin", "adminpass") is not None
    assert session_has_admin_role() is True
    assert session_is_super_admin() is True


def test_cannot_delete_system_roles(monkeypatch):
    _wire_memory_store(monkeypatch)
    auth = AuthService()
    auth.ensure_default_admin("adminpass")
    with pytest.raises(ValueError, match="système"):
        auth.remove_role("operateur")
    with pytest.raises(ValueError, match="système"):
        auth.remove_role("lecteur")


# These units use in-memory configuration; atomic SQL/audit rollback is tested natively.
import pytest
from contextlib import nullcontext

@pytest.fixture(autouse=True)
def isolated_auth_transaction(monkeypatch):
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_data_write',lambda *a,**kw:nullcontext())
    monkeypatch.setattr('sme_gend.services.audit_log._append_audit_mysql',lambda **kw:None)
    # This suite isolates configuration behavior; native tests exercise the registry.
    monkeypatch.setattr('sme_gend.services.auth_session_registry.try_register_session',lambda *a,**kw:('SYNTHETIC',None))
    monkeypatch.setattr('sme_gend.services.auth_session_registry.heartbeat_session',lambda *a,**kw:True)
    monkeypatch.setattr('sme_gend.services.auth_session_registry.unregister_session',lambda *a,**kw:None)
    monkeypatch.setattr('sme_gend.services.auth_session_registry.revoke_user_sessions',lambda *a,**kw:0)
    monkeypatch.setattr('sme_gend.services.auth_session_registry.advance_session_revision',lambda *a,**kw:None)
