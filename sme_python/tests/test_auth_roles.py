"""Tests rôles et permissions dynamiques."""

from __future__ import annotations

from sme_gend.config.auth_config import AuthConfig, AuthRole, AuthUser
from sme_gend.config.auth_permissions import (
    ADMIN_ONLY_PERMISSIONS,
    ROLE_ADMIN,
    ROLE_OPERATEUR,
    ROLE_SUPER_ADMIN,
    admin_role_permissions,
    default_roles_seed,
    is_super_admin_role,
    is_system_protected_role,
    lecteur_role_permissions,
    operateur_role_permissions,
    reconcile_system_roles,
    reconcile_system_users,
    sanitize_role_permissions,
)
from sme_gend.services.auth_service import AuthService, role_grants_permission


def test_role_grants_wildcard():
    role = AuthRole(id=ROLE_SUPER_ADMIN, name="Super Admin", permissions=["*"])
    assert role_grants_permission(role, "admin.users")
    assert role_grants_permission(role, "attestations.doc_log")
    assert role_grants_permission(role, "anything")


def test_role_grants_explicit():
    role = AuthRole(id="lecteur", name="Lecteur", permissions=["effectifs.liste"])
    assert role_grants_permission(role, "effectifs.liste")
    assert not role_grants_permission(role, "admin.users")
    assert not role_grants_permission(role, "attestations.doc_log")


def test_admin_only_permissions_exclude_operateur():
    op_perms = set(operateur_role_permissions())
    for pid in ADMIN_ONLY_PERMISSIONS:
        assert pid not in op_perms


def test_lecteur_has_no_doc_log():
    assert "attestations.doc_log" not in lecteur_role_permissions()
    assert "admin.users" not in lecteur_role_permissions()
    assert "workflow.pilotage" not in lecteur_role_permissions()
    assert "effectifs.edition" not in lecteur_role_permissions()
    assert "effectifs.presents" not in lecteur_role_permissions()
    assert "detaches.view" not in lecteur_role_permissions()


def test_operateur_cannot_get_admin_users_subperms():
    from sme_gend.config.auth_permissions import ADMIN_USERS_ALL

    op_perms = set(operateur_role_permissions())
    for pid in ADMIN_USERS_ALL:
        assert pid not in op_perms


def test_operateur_has_attestation_history():
    assert "attestations.history" in operateur_role_permissions()
    assert "effectifs.edition" in operateur_role_permissions()
    assert "effectifs.sme_dpf" in operateur_role_permissions()
    assert "attestations.doc_log" not in operateur_role_permissions()


def test_reconcile_preserves_removed_operateur_permissions():
    cfg = AuthConfig()
    perms = [
        p
        for p in operateur_role_permissions()
        if p not in ("attestations.history", "effectifs.edition", "effectifs.sme_dpf")
    ]
    cfg.roles = [
        AuthRole(id=ROLE_OPERATEUR, name="Op", permissions=perms),
    ]
    assert reconcile_system_roles(cfg.roles)
    op = cfg.role_by_id(ROLE_OPERATEUR)
    assert op is not None
    assert "attestations.history" not in op.permissions
    assert "effectifs.edition" not in op.permissions
    assert "effectifs.sme_dpf" not in op.permissions


def test_reconcile_does_not_regrant_attestation_history():
    cfg = AuthConfig()
    perms = [p for p in operateur_role_permissions() if p != "attestations.history"]
    cfg.roles = [
        AuthRole(id=ROLE_OPERATEUR, name="Op", permissions=perms),
    ]
    assert reconcile_system_roles(cfg.roles)
    op = cfg.role_by_id(ROLE_OPERATEUR)
    assert op is not None
    assert "attestations.history" not in op.permissions


def test_admin_has_doc_log():
    assert "attestations.doc_log" in admin_role_permissions()
    assert "admin.edit_age" in admin_role_permissions()


def test_operateur_cannot_edit_age():
    assert "admin.edit_age" not in operateur_role_permissions()


def test_system_roles_seed():
    by_id = {r["id"]: r for r in default_roles_seed()}
    assert ROLE_SUPER_ADMIN in by_id
    assert by_id[ROLE_SUPER_ADMIN]["permissions"] == ["*"]
    assert "*" not in by_id[ROLE_ADMIN]["permissions"]
    assert "attestations.doc_log" in by_id[ROLE_ADMIN]["permissions"]


def test_reconcile_migrates_admin_wildcard_and_bootstrap_user():
    cfg = AuthConfig()
    cfg.roles = [
        AuthRole(id=ROLE_ADMIN, name="Admin", permissions=["*"]),
        AuthRole(id=ROLE_OPERATEUR, name="Op", permissions=operateur_role_permissions()),
    ]
    cfg.users = [
        AuthUser(
            username="admin",
            password_hash="x",
            salt="y",
            role=ROLE_ADMIN,
        )
    ]
    assert reconcile_system_roles(cfg.roles)
    admin = cfg.role_by_id(ROLE_ADMIN)
    assert admin is not None
    assert "*" not in admin.permissions
    assert "attestations.doc_log" in admin.permissions
    assert cfg.role_by_id(ROLE_SUPER_ADMIN) is not None
    assert reconcile_system_users(cfg.users, legacy_bootstrap=True)
    assert cfg.users[0].role == ROLE_SUPER_ADMIN


def test_protected_roles():
    assert is_system_protected_role(ROLE_SUPER_ADMIN)
    assert is_system_protected_role(ROLE_ADMIN)
    assert is_system_protected_role(ROLE_OPERATEUR)
    assert is_system_protected_role("lecteur")
    assert is_super_admin_role(ROLE_SUPER_ADMIN)


def test_custom_role_cannot_keep_doc_log():
    perms = sanitize_role_permissions(
        "chef_section",
        ["dashboard.view", "attestations.doc_log", "effectifs.liste"],
    )
    assert "attestations.doc_log" not in perms
    assert "effectifs.liste" in perms


def test_add_role_and_permission(monkeypatch):
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

    auth = AuthService()
    if auth.config.role_by_id("custom"):
        auth.remove_role("custom")
    auth.add_role("custom", "Personnalisé", permissions=["dashboard.view"])
    auth.reload_config()
    role = auth.config.role_by_id("custom")
    assert role is not None
    assert "dashboard.view" in role.permissions


def test_sanitize_locks_system_role_permissions():
    assert sanitize_role_permissions(ROLE_SUPER_ADMIN, ["dashboard.view"]) == ["*"]
    admin = sanitize_role_permissions(ROLE_ADMIN, ["dashboard.view"])
    assert "admin.users" in admin
    assert set(admin) == set(admin_role_permissions())
    op = sanitize_role_permissions(
        ROLE_OPERATEUR,
        ["dashboard.view", "admin.users", "effectifs.sme_dpf"],
    )
    assert "dashboard.view" in op
    assert "effectifs.sme_dpf" in op
    assert "admin.users" not in op


def test_custom_role_rejects_wildcard():
    import pytest

    with pytest.raises(ValueError, match="joker"):
        sanitize_role_permissions("chef_section", ["*"])


# These units use in-memory configuration; atomic SQL/audit rollback is tested natively.
import pytest
from contextlib import nullcontext

@pytest.fixture(autouse=True)
def isolated_auth_transaction(monkeypatch):
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_data_write',lambda *a,**kw:nullcontext())
    monkeypatch.setattr('sme_gend.services.audit_log._append_audit_mysql',lambda **kw:None)
