"""Configuration authentification locale (activable / désactivable)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sme_gend.config.auth_permissions import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    default_roles_seed,
    merge_permission_catalog,
    reconcile_system_roles,
    reconcile_system_users,
)
from sme_gend.config.user_paths import user_application_data_dir

_log = logging.getLogger(__name__)

# Authentification désactivée par défaut ; compte initial créé au premier lancement si absent.
DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "<DEFINIR_LOCALEMENT_AVANT_UTILISATION>"  # Secret retire de la sauvegarde publique.

MIN_SESSION_TIMEOUT_MINUTES = 30
MAX_SESSION_TIMEOUT_MINUTES = 1440
DEFAULT_SESSION_TIMEOUT_MINUTES = 480

MIN_MAX_FAILED_ATTEMPTS = 3
MAX_MAX_FAILED_ATTEMPTS = 20
DEFAULT_MAX_FAILED_ATTEMPTS = 5

MIN_LOCKOUT_MINUTES = 5
MAX_LOCKOUT_MINUTES = 120
DEFAULT_LOCKOUT_MINUTES = 15

CONCURRENT_SESSIONS_OFF = "off"
CONCURRENT_SESSIONS_SINGLE = "single"
CONCURRENT_SESSIONS_LIMITED = "limited"
CONCURRENT_SESSIONS_MODES = (
    CONCURRENT_SESSIONS_OFF,
    CONCURRENT_SESSIONS_SINGLE,
    CONCURRENT_SESSIONS_LIMITED,
)

MIN_CONCURRENT_SESSIONS = 2
MAX_CONCURRENT_SESSIONS = 20
DEFAULT_MAX_CONCURRENT_SESSIONS = 2


def auth_config_file() -> Path:
    return user_application_data_dir() / "auth_config.json"


def clamp_session_timeout(value: Any, default: int = DEFAULT_SESSION_TIMEOUT_MINUTES) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = default
    return max(MIN_SESSION_TIMEOUT_MINUTES, min(MAX_SESSION_TIMEOUT_MINUTES, minutes))


def clamp_max_failed_attempts(value: Any, default: int = DEFAULT_MAX_FAILED_ATTEMPTS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(MIN_MAX_FAILED_ATTEMPTS, min(MAX_MAX_FAILED_ATTEMPTS, n))


def clamp_lockout_minutes(value: Any, default: int = DEFAULT_LOCKOUT_MINUTES) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = default
    return max(MIN_LOCKOUT_MINUTES, min(MAX_LOCKOUT_MINUTES, minutes))


def normalize_concurrent_sessions_mode(value: Any) -> str:
    mode = str(value or CONCURRENT_SESSIONS_OFF).strip().lower()
    if mode in ("0", "false", "none", "unlimited", "disabled"):
        return CONCURRENT_SESSIONS_OFF
    if mode in ("1", "one", "unique"):
        return CONCURRENT_SESSIONS_SINGLE
    if mode not in CONCURRENT_SESSIONS_MODES:
        return CONCURRENT_SESSIONS_OFF
    return mode


def clamp_concurrent_sessions_limit(value: Any, default: int = DEFAULT_MAX_CONCURRENT_SESSIONS) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(MIN_CONCURRENT_SESSIONS, min(MAX_CONCURRENT_SESSIONS, n))


@dataclass
class AuthUser:
    username: str
    password_hash: str
    salt: str
    display_name: str = ""
    role: str = "operateur"
    failed_attempts: int = 0
    locked_until: str = ""
    disabled: bool = False
    created_at: str = ""
    last_login_at: str = ""
    password_changed_at: str = ""
    session_rev: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "password_hash": self.password_hash,
            "salt": self.salt,
            "display_name": self.display_name,
            "role": self.role,
            "failed_attempts": self.failed_attempts,
            "locked_until": self.locked_until,
            "disabled": self.disabled,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
            "password_changed_at": self.password_changed_at,
            "session_rev": self.session_rev,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuthUser:
        role_raw = str(d.get("role", "operateur")).strip() or "operateur"
        if role_raw == "user":
            role_raw = "operateur"
        try:
            failed = int(d.get("failed_attempts", 0) or 0)
        except (TypeError, ValueError):
            failed = 0
        try:
            session_rev = int(d.get("session_rev", 0) or 0)
        except (TypeError, ValueError):
            session_rev = 0
        return cls(
            username=str(d.get("username", "")).strip(),
            password_hash=str(d.get("password_hash", "")),
            salt=str(d.get("salt", "")),
            display_name=str(d.get("display_name", "")).strip(),
            role=role_raw,
            failed_attempts=failed,
            locked_until=str(d.get("locked_until", "") or ""),
            disabled=bool(d.get("disabled", False)),
            created_at=str(d.get("created_at", "") or "").strip(),
            last_login_at=str(d.get("last_login_at", "") or "").strip(),
            password_changed_at=str(d.get("password_changed_at", "") or "").strip(),
            session_rev=max(0, session_rev),
        )


@dataclass
class AuthRole:
    id: str
    name: str
    description: str = ""
    permissions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "permissions": list(self.permissions),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuthRole | None:
        rid = str(d.get("id", "")).strip()
        if not rid:
            return None
        perms_raw = d.get("permissions")
        perms: list[str] = []
        if isinstance(perms_raw, list):
            perms = [str(p).strip() for p in perms_raw if str(p).strip()]
        return cls(
            id=rid,
            name=str(d.get("name", rid)).strip() or rid,
            description=str(d.get("description", "")).strip(),
            permissions=perms,
        )


@dataclass
class AuthConfig:
    config_rev: int = 0
    enabled: bool = False
    session_timeout_minutes: int = DEFAULT_SESSION_TIMEOUT_MINUTES
    remember_last_username: bool = True
    max_failed_attempts: int = DEFAULT_MAX_FAILED_ATTEMPTS
    lockout_minutes: int = DEFAULT_LOCKOUT_MINUTES
    concurrent_sessions_mode: str = CONCURRENT_SESSIONS_OFF
    max_concurrent_sessions: int = DEFAULT_MAX_CONCURRENT_SESSIONS
    roles_rev: int = 0
    auth_policy_version: int = 1
    last_username: str = ""
    users: list[AuthUser] = field(default_factory=list)
    roles: list[AuthRole] = field(default_factory=list)
    permission_catalog: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "config_rev": self.config_rev,
            "session_timeout_minutes": self.session_timeout_minutes,
            "remember_last_username": self.remember_last_username,
            "max_failed_attempts": self.max_failed_attempts,
            "lockout_minutes": self.lockout_minutes,
            "concurrent_sessions_mode": self.concurrent_sessions_mode,
            "max_concurrent_sessions": self.max_concurrent_sessions,
            "roles_rev": self.roles_rev,
            "auth_policy_version": self.auth_policy_version,
            "last_username": self.last_username,
            "users": [u.to_dict() for u in self.users],
            "roles": [r.to_dict() for r in self.roles],
            "permission_catalog": self.permission_catalog,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuthConfig:
        users: list[AuthUser] = []
        users_raw = d.get("users")
        if isinstance(users_raw, list):
            for item in users_raw:
                if isinstance(item, dict) and item.get("username"):
                    users.append(AuthUser.from_dict(item))

        roles: list[AuthRole] = []
        roles_raw = d.get("roles")
        if isinstance(roles_raw, list):
            for item in roles_raw:
                if isinstance(item, dict):
                    role = AuthRole.from_dict(item)
                    if role:
                        roles.append(role)

        catalog: list[dict[str, Any]] = []
        catalog_raw = d.get("permission_catalog")
        if isinstance(catalog_raw, list):
            for item in catalog_raw:
                if isinstance(item, dict) and item.get("id"):
                    catalog.append(dict(item))

        try:
            roles_rev = int(d.get("roles_rev", 0) or 0)
        except (TypeError, ValueError):
            roles_rev = 0

        cfg = cls(
            enabled=bool(d.get("enabled", False)),
            session_timeout_minutes=clamp_session_timeout(
                d.get("session_timeout_minutes", DEFAULT_SESSION_TIMEOUT_MINUTES)
            ),
            remember_last_username=bool(d.get("remember_last_username", True)),
            max_failed_attempts=clamp_max_failed_attempts(
                d.get("max_failed_attempts", DEFAULT_MAX_FAILED_ATTEMPTS)
            ),
            lockout_minutes=clamp_lockout_minutes(
                d.get("lockout_minutes", DEFAULT_LOCKOUT_MINUTES)
            ),
            concurrent_sessions_mode=normalize_concurrent_sessions_mode(
                d.get("concurrent_sessions_mode", CONCURRENT_SESSIONS_OFF)
            ),
            max_concurrent_sessions=clamp_concurrent_sessions_limit(
                d.get("max_concurrent_sessions", DEFAULT_MAX_CONCURRENT_SESSIONS)
            ),
            roles_rev=max(0, roles_rev),
            last_username=str(d.get("last_username", "")).strip(),
            users=users,
            roles=roles,
            permission_catalog=catalog,
        )
        cfg.config_rev = max(0, int(d.get("config_rev", 0)))
        # Old installations had only an initial admin. Migrate once on loading
        # an unversioned payload, never when saving or creating modern accounts.
        if "auth_policy_version" not in d:
            reconcile_system_users(cfg.users, default_admin_username=DEFAULT_ADMIN_USERNAME,
                                   legacy_bootstrap=True)
            # The old default bundled backup and restore into the operator role.
            for role in cfg.roles:
                if role.id == "operateur":
                    role.permissions = [p for p in role.permissions if p != "system.backup"]
            for permission in cfg.permission_catalog:
                if permission.get("id") == "system.backup":
                    permission["label"] = "Créer et consulter des sauvegardes"
                    permission["description"] = "N'autorise pas la restauration."
        cfg.ensure_roles_and_catalog()
        return cfg

    def ensure_roles_and_catalog(self) -> None:
        """Rôles et catalogue par défaut si absents (migration anciens fichiers)."""
        if not self.roles:
            for raw in default_roles_seed():
                role = AuthRole.from_dict(raw)
                if role:
                    self.roles.append(role)

        if not self.permission_catalog:
            self.permission_catalog = [p.to_dict() for p in merge_permission_catalog(None)]

        role_ids = {r.id for r in self.roles}
        for raw in default_roles_seed():
            rid = str(raw.get("id", "")).strip()
            if rid and rid not in role_ids:
                role = AuthRole.from_dict(raw)
                if role:
                    self.roles.append(role)
                    role_ids.add(rid)

        reconcile_system_roles(self.roles)

        role_ids = {r.id for r in self.roles}
        for u in self.users:
            if u.role not in role_ids:
                if u.role in (ROLE_ADMIN, ROLE_SUPER_ADMIN) and ROLE_SUPER_ADMIN in role_ids:
                    u.role = ROLE_SUPER_ADMIN
                    continue
                u.role = "operateur" if "operateur" in role_ids else next(iter(role_ids))

    def role_by_id(self, role_id: str) -> AuthRole | None:
        rid = (role_id or "").strip()
        return next((r for r in self.roles if r.id == rid), None)

    def effective_permission_catalog(self):
        return merge_permission_catalog(self.permission_catalog or None)


def load_auth_config(*, strict: bool = False) -> AuthConfig:
    try:
        from sme_gend.config import get_settings
        from sme_gend.services.app_config_store import KEY_AUTH, load_config_dict

        raw = (load_config_dict(get_settings(), KEY_AUTH, strict=True) if strict
               else load_config_dict(get_settings(), KEY_AUTH))
        if raw:
            return AuthConfig.from_dict(raw)
    except Exception as exc:
        if strict:
            raise RuntimeError("Vérification de la configuration des comptes indisponible.") from exc
        _log.warning("auth_config MySQL : %s", exc)
    cfg = AuthConfig()
    cfg.ensure_roles_and_catalog()
    return cfg


def save_auth_config(cfg: AuthConfig) -> None:
    cfg.ensure_roles_and_catalog()
    from sme_gend.config import get_settings
    from sme_gend.services.app_config_store import KEY_AUTH, save_config_dict

    payload = cfg.to_dict()
    payload["config_rev"] = cfg.config_rev + 1
    save_config_dict(get_settings(), KEY_AUTH, payload, expected_revision=cfg.config_rev)
    cfg.config_rev = payload["config_rev"]
