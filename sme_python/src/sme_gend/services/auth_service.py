"""Authentification locale (PBKDF2, sans dépendance externe)."""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass, replace
from sme_gend.services.auth_write import atomic_auth_change, audited_login
from datetime import datetime, timedelta, timezone

from sme_gend.config.auth_config import (
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
    AuthConfig,
    AuthRole,
    AuthUser,
    clamp_lockout_minutes,
    clamp_max_failed_attempts,
    clamp_session_timeout,
    load_auth_config,
    save_auth_config,
)
from sme_gend.config.auth_permissions import (
    ROLE_ADMIN,
    ROLE_SUPER_ADMIN,
    PermissionDef,
    is_admin_rank_role,
    is_super_admin_role,
    is_system_protected_role,
    sanitize_role_permissions,
)

_PBKDF2_ITERATIONS = 260_000
_LOG = logging.getLogger(__name__)
_ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{1,31}$")
MIN_PASSWORD_LENGTH = 8


@dataclass(frozen=True, slots=True)
class AuthSession:
    username: str
    display_name: str
    role: str
    expires_at: datetime
    must_change_password: bool = False
    session_rev: int = 0
    roles_rev: int = 0
    session_token: str = ""


def _hash_password(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return dk.hex()


def hash_new_password(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    salt_hex = salt.hex()
    return _hash_password(password, salt_hex), salt_hex


def verify_password(password: str, user: AuthUser) -> bool:
    if not user.password_hash or not user.salt:
        return False
    return secrets.compare_digest(_hash_password(password, user.salt), user.password_hash)


def validate_password(password: str, *, allow_default: bool = False) -> str:
    pwd = password or ""
    if len(pwd) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
        )
    if not allow_default and pwd == DEFAULT_ADMIN_PASSWORD:
        raise ValueError("Choisissez un mot de passe différent du mot de passe par défaut.")
    return pwd


def role_grants_permission(role: AuthRole | None, permission_id: str) -> bool:
    if role is None:
        return False
    if "*" in role.permissions:
        return True
    pid = (permission_id or "").strip()
    if pid in role.permissions:
        return True
    if pid.startswith("admin.users.") and "admin.users" in role.permissions:
        return True
    return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_is_login_blocked(user: AuthUser) -> bool:
    locked_until = AuthService._parse_locked_until(user.locked_until)
    if locked_until is None:
        return False
    return datetime.now(timezone.utc) < locked_until


class AuthService:
    def __init__(self) -> None:
        self._config = load_auth_config()
        self._session: AuthSession | None = None
        self._session_verification_pending = False
        self._enforced = False
        self.last_login_error: str | None = None

    @property
    def config(self) -> AuthConfig:
        return self._config

    def reload_config(self) -> None:
        self._config = load_auth_config(strict=True) if self.is_enforced() else load_auth_config()

    def replace_config(self, cfg: AuthConfig) -> None:
        """Applique une config déjà chargée (évite une 2e requête MySQL)."""
        self._config = cfg

    def permission_catalog(self) -> list[PermissionDef]:
        return self._config.effective_permission_catalog()

    def roles(self) -> list[AuthRole]:
        self._config.ensure_roles_and_catalog()
        return list(self._config.roles)

    def is_enabled(self) -> bool:
        """Réglage persisté (écran de connexion au prochain lancement)."""
        return bool(self._config.enabled)

    def is_enforced(self) -> bool:
        """True si CE processus a exigé un login (contrôle d'accès actif)."""
        return bool(self._enforced)

    def mark_enforced(self) -> None:
        self._enforced = True

    def has_users(self) -> bool:
        return any((u.username or "").strip() for u in self._config.users)

    def uses_default_admin_password(self) -> bool:
        user = self._user_by_name(DEFAULT_ADMIN_USERNAME)
        if user is None:
            return False
        return verify_password(DEFAULT_ADMIN_PASSWORD, user)

    def _validate_role_id(self, role_id: str) -> str:
        rid = (role_id or "").strip()
        if not rid:
            raise ValueError("Identifiant de rôle requis.")
        if not _ROLE_ID_RE.match(rid):
            raise ValueError(
                "Identifiant de rôle invalide (minuscules, chiffres, underscore, 2–32 car.)."
            )
        return rid

    def _ensure_role_exists(self, role_id: str) -> None:
        if self._config.role_by_id(role_id) is None:
            raise ValueError(f"Le rôle « {role_id} » n'existe pas.")

    def _user_by_name(self, username: str) -> AuthUser | None:
        key = (username or "").strip().casefold()
        if not key:
            return None
        return next(
            (u for u in self._config.users if u.username.casefold() == key),
            None,
        )

    def get_user(self, username: str) -> AuthUser | None:
        return self._user_by_name(username)

    def _count_role(self, role_id: str) -> int:
        return sum(1 for u in self._config.users if u.role == role_id)

    def _session_actor_role(self) -> str | None:
        session = self.current_session()
        if session is None:
            return None
        actor = self._user_by_name(session.username)
        return actor.role if actor is not None else None

    def _session_actor_username(self) -> str | None:
        session = self.current_session()
        if session is None:
            return None
        return session.username

    def _require_can_assign_role(self, role_id: str) -> None:
        if not self._config.enabled:
            return
        actor = self._session_actor_role()
        if actor is None:
            raise ValueError("Une session authentifiée est requise pour attribuer un rôle.")
        if is_super_admin_role(role_id) and not is_super_admin_role(actor):
            raise ValueError("Seul un super administrateur peut attribuer ce rôle.")
        if role_id == ROLE_ADMIN and not is_admin_rank_role(actor):
            raise ValueError("Vous n'avez pas le droit d'attribuer le rôle administrateur.")

    def _assert_not_last_super_admin(self, user: AuthUser, *, new_role: str | None) -> None:
        if user.role != ROLE_SUPER_ADMIN:
            return
        if new_role == ROLE_SUPER_ADMIN:
            return
        remaining = [u for u in self._config.users
                     if u.username.casefold() != user.username.casefold()
                     and u.role == ROLE_SUPER_ADMIN and not u.disabled
                     and not _user_is_login_blocked(u)]
        if not remaining:
            raise ValueError("Impossible de retirer le dernier super administrateur actif.")

    def _assert_can_modify_user(self, user: AuthUser) -> None:
        actor_role = self._session_actor_role()
        if not self._config.enabled or actor_role is None:
            return
        if is_super_admin_role(user.role) and not is_super_admin_role(actor_role):
            raise ValueError("Seul un super administrateur peut modifier ce compte.")
        if user.role == ROLE_ADMIN and not is_admin_rank_role(actor_role):
            raise ValueError("Seul un administrateur peut modifier ce compte.")

    def _assert_users_admin_perm(self, permission_id: str) -> None:
        self.reload_config()
        if not self._config.enabled and not self.is_enforced():
            return
        session = self.current_session()
        if session is None:
            raise ValueError("Une session authentifiée est requise pour cette action.")
        actor = self._user_by_name(session.username)
        if actor is None or actor.disabled or _user_is_login_blocked(actor) or actor.session_rev != session.session_rev:
            raise ValueError("La session n'est plus autorisée. Reconnectez-vous.")
        role = self._config.role_by_id(actor.role)
        if not role_grants_permission(role, "admin.users") and not role_grants_permission(role, permission_id):
            raise ValueError("Permission insuffisante pour cette action.")

    def _max_failed_attempts(self) -> int:
        return clamp_max_failed_attempts(self._config.max_failed_attempts)

    def _lockout_minutes(self) -> int:
        return clamp_lockout_minutes(self._config.lockout_minutes)

    def _bump_user_session_rev(self, user: AuthUser) -> None:
        user.session_rev = int(user.session_rev or 0) + 1
        self._sync_registry_after_rev_bump(user)

    def _sync_registry_after_rev_bump(self, user: AuthUser) -> None:
        from sme_gend.services.auth_session_registry import advance_session_revision, revoke_user_sessions

        settings = self._settings()
        session = self._session
        token = None
        if session is not None and session.username.casefold() == user.username.casefold():
            token = (session.session_token or "").strip() or None
        revoke_user_sessions(settings, user.username, except_token=token)
        if token:
            advance_session_revision(settings, session_token=token, username=user.username,
                                     previous_revision=session.session_rev, revision=user.session_rev)

    def _settings(self):
        from sme_gend.config import get_settings

        return get_settings()

    def _bump_roles_rev(self) -> None:
        self._config.roles_rev = int(self._config.roles_rev or 0) + 1

    def sync_session_from_config(self) -> str:
        """
        Recharge la config MySQL et aligne la session locale.
        Retourne ``ok``, ``updated`` ou ``invalid`` (session fermée).
        """
        if not self.is_enforced() or self._session is None:
            return "ok"
        prev = self._session
        if self.session_is_expired():
            self._session = None
            self._session_verification_pending = False
            return "invalid"
        try:
            self.reload_config()
        except Exception:
            self._session_verification_pending = True
            raise
        user = self._user_by_name(prev.username)
        if user is None or user.disabled or _user_is_login_blocked(user):
            self._session = None
            return "invalid"
        from sme_gend.services.auth_session_registry import heartbeat_session

        if user.session_rev != prev.session_rev:
            self._session = None
            return "invalid"
        try:
            ok = heartbeat_session(
                self._settings(),
                session_token=prev.session_token,
                username=prev.username,
                session_rev=user.session_rev,
                expires_at=prev.expires_at,
            )
        except Exception:
            self._session_verification_pending = True
            raise
        if not ok:
            self._session = None
            return "invalid"
        self._session_verification_pending = False
        stale = (
            user.session_rev != prev.session_rev
            or self._config.roles_rev != prev.roles_rev
            or user.role != prev.role
            or (user.display_name or user.username) != prev.display_name
        )
        if stale:
            self._session = AuthSession(
                username=user.username,
                display_name=user.display_name or user.username,
                role=user.role or "operateur",
                expires_at=prev.expires_at,
                must_change_password=prev.must_change_password,
                session_rev=user.session_rev,
                roles_rev=self._config.roles_rev,
                session_token=prev.session_token,
            )
            return "updated"
        return "ok"

    def current_session(self) -> AuthSession | None:
        if self._session is None:
            return None
        if datetime.now(timezone.utc) >= self._session.expires_at:
            self._session = None
            return None
        if getattr(self, '_session_verification_pending', False):
            return None
        return self._session

    def record_activity(self, *, at: datetime | None = None) -> AuthSession | None:
        """Renouvelle localement l'expiration après une action utilisateur."""
        session = self.current_session()
        if session is None:
            return None
        now = at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        expires = now + timedelta(
            minutes=clamp_session_timeout(self._config.session_timeout_minutes)
        )
        self._session = replace(session, expires_at=expires)
        return self._session

    def seconds_until_expiry(self) -> int | None:
        """Secondes restantes avant expiration (0 si expirée), None si pas de session."""
        if self._session is None:
            return None
        delta = (self._session.expires_at - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))

    def session_is_expired(self) -> bool:
        """True si une session existe en mémoire mais a dépassé ``expires_at``."""
        if self._session is None:
            return False
        return datetime.now(timezone.utc) >= self._session.expires_at

    def invalidate_expired_session(self) -> bool:
        """Efface la session si expirée. Retourne True si une session a été invalidée."""
        if not self.session_is_expired():
            return False
        self._session = None
        return True

    def session_has_permission(self, permission_id: str) -> bool:
        """Vérifie la permission pour la session courante (auth désactivée = tout autorisé)."""
        if not self.is_enforced():
            return True
        session = self.current_session()
        if session is None:
            return False
        role = self._config.role_by_id(session.role)
        return role_grants_permission(role, permission_id)

    @audited_login
    def authenticate(self, username: str, password: str) -> AuthSession | None:
        self.reload_config()
        self.last_login_error = None
        uname = (username or "").strip()
        if not uname or not password:
            return None

        user = self._user_by_name(uname)
        if user is None:
            self._audit(action="login_failed", detail=uname, actor=uname)
            self.last_login_error = "Identifiant ou mot de passe incorrect."
            return None

        if user.disabled:
            self._audit(
                action="login_blocked",
                detail=f"{user.username} (désactivé)",
                actor=user.username,
            )
            self.last_login_error = "Compte désactivé — contactez un administrateur."
            return None

        max_attempts = self._max_failed_attempts()
        lockout_min = self._lockout_minutes()
        locked_until = self._parse_locked_until(user.locked_until)
        if locked_until is not None and datetime.now(timezone.utc) < locked_until:
            remaining = max(
                1, int((locked_until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            )
            self._audit(
                action="login_blocked",
                detail=user.username,
                actor=user.username,
            )
            if (locked_until - datetime.now(timezone.utc)).total_seconds() > 86400 * 365:
                self.last_login_error = "Compte verrouillé par un administrateur."
            else:
                self.last_login_error = (
                    f"Compte verrouillé suite à plusieurs échecs. Réessayez dans "
                    f"{remaining} minute(s)."
                )
            return None

        if not verify_password(password, user):
            user.failed_attempts = int(user.failed_attempts or 0) + 1
            if user.failed_attempts >= max_attempts:
                lock_until = datetime.now(timezone.utc) + timedelta(minutes=lockout_min)
                user.locked_until = lock_until.isoformat()
                self.last_login_error = (
                    f"Compte verrouillé après {max_attempts} échecs. "
                    f"Réessayez dans {lockout_min} minute(s)."
                )
            else:
                self.last_login_error = "Identifiant ou mot de passe incorrect."
            save_auth_config(self._config)
            self._audit(
                action="login_failed",
                detail=f"{user.username} (tentative {user.failed_attempts}/{max_attempts})",
                actor=user.username,
            )
            return None

        if user.failed_attempts or user.locked_until:
            user.failed_attempts = 0
            user.locked_until = ""
        user.last_login_at = _utc_now_iso()
        if self._config.remember_last_username:
            self._config.last_username = user.username
        save_auth_config(self._config)

        minutes = clamp_session_timeout(self._config.session_timeout_minutes)
        expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        role_id = user.role or "operateur"
        must_change = (
            uname.casefold() == DEFAULT_ADMIN_USERNAME.casefold()
            and password == DEFAULT_ADMIN_PASSWORD
        )
        from sme_gend.services.auth_client_instance import client_instance_id, machine_label
        from sme_gend.services.auth_session_registry import try_register_session

        session_token, reg_err = try_register_session(
            self._settings(),
            self._config,
            username=user.username,
            session_rev=user.session_rev,
            client_instance_id=client_instance_id(),
            machine_label=machine_label(),
            expires_at=expires,
        )
        if reg_err:
            self.last_login_error = reg_err
            self._audit(
                action="login_blocked",
                detail=f"{user.username} (limite postes)",
                actor=user.username,
            )
            return None
        self._session = AuthSession(
            username=user.username,
            display_name=user.display_name or user.username,
            role=role_id,
            expires_at=expires,
            must_change_password=must_change,
            session_rev=user.session_rev,
            roles_rev=self._config.roles_rev,
            session_token=session_token or "",
        )
        if self._config.enabled:
            self._enforced = True
        self._session_verification_pending = False
        self._audit(
            action="login",
            detail=user.username,
            actor=user.username,
        )
        return self._session

    @staticmethod
    def _parse_locked_until(raw: str) -> datetime | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _audit(self, *, action: str, detail: str, actor: str | None = None) -> None:
        from sme_gend.services.audit_log import append_user_activity

        resolved = (actor or "").strip()
        if not resolved:
            session = self._session
            from sme_gend.services.auth_context import resolve_write_actor
            resolved = resolve_write_actor(session.username if session is not None else None)
        append_user_activity(
            action=action,
            module="auth",
            detail=detail,
            actor=resolved or "non identifié",
        )

    def logout(self) -> None:
        session = self._session
        if session is not None:
            try:
                from sme_gend.services.auth_session_registry import unregister_session

                unregister_session(self._settings(), session.session_token)
            except Exception:
                _LOG.exception(
                    "Impossible de supprimer du registre MySQL la session de %s.",
                    session.username,
                )
            try:
                self._audit(
                    action="logout",
                    detail=session.username,
                    actor=session.username,
                )
            finally:
                self._session = None
        else:
            self._session = None

    @atomic_auth_change
    def lock_user(self, username: str) -> None:
        """Verrouillage administratif jusqu'à déverrouillage ou changement de mot de passe."""
        self._assert_users_admin_perm("admin.users.accounts")
        self.reload_config()
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        actor = self._session_actor_username()
        if actor and actor.casefold() == user.username.casefold():
            raise ValueError("Vous ne pouvez pas verrouiller votre propre compte.")
        lock_until = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        user.failed_attempts = self._max_failed_attempts()
        user.locked_until = lock_until.isoformat()
        self._bump_user_session_rev(user)
        save_auth_config(self._config)
        self._audit(action="user_locked", detail=user.username)

    @atomic_auth_change
    def save_auth_settings(
        self,
        *,
        enabled: bool,
        session_timeout_minutes: int,
        remember_last_username: bool | None = None,
        max_failed_attempts: int | None = None,
        lockout_minutes: int | None = None,
        concurrent_sessions_mode: str | None = None,
        max_concurrent_sessions: int | None = None,
    ) -> None:
        from sme_gend.config.auth_config import (
            clamp_concurrent_sessions_limit,
            normalize_concurrent_sessions_mode,
        )

        self._assert_users_admin_perm("admin.users.settings")
        if (bool(enabled) != self._config.enabled
                and (self._config.enabled or self.is_enforced())
                and not is_super_admin_role(self._session_actor_role())):
            raise ValueError("Seul un super administrateur peut activer ou désactiver l'authentification.")
        self._config.enabled = enabled
        self._config.session_timeout_minutes = clamp_session_timeout(session_timeout_minutes)
        if remember_last_username is not None:
            self._config.remember_last_username = bool(remember_last_username)
        if max_failed_attempts is not None:
            self._config.max_failed_attempts = clamp_max_failed_attempts(max_failed_attempts)
        if lockout_minutes is not None:
            self._config.lockout_minutes = clamp_lockout_minutes(lockout_minutes)
        if concurrent_sessions_mode is not None:
            self._config.concurrent_sessions_mode = normalize_concurrent_sessions_mode(
                concurrent_sessions_mode
            )
        if max_concurrent_sessions is not None:
            self._config.max_concurrent_sessions = clamp_concurrent_sessions_limit(
                max_concurrent_sessions
            )
        if self._config.enabled and not self.has_users():
            raise ValueError("Créez au moins un utilisateur avant d'activer l'authentification.")
        save_auth_config(self._config)
        if not enabled:
            # Prend effet immédiatement : plus de contrôle d'accès dans ce processus.
            self._enforced = False
            self.logout()
        detail = (
            f"enabled={enabled} timeout={self._config.session_timeout_minutes} "
            f"remember={self._config.remember_last_username} "
            f"attempts={self._config.max_failed_attempts} "
            f"lockout={self._config.lockout_minutes}min "
            f"sessions={self._config.concurrent_sessions_mode} "
            f"max_postes={self._config.max_concurrent_sessions}"
        )
        self._audit(action="auth_settings", detail=detail)

    def list_active_sessions(self, *, username: str | None = None):
        from sme_gend.services.auth_session_registry import list_active_sessions

        self._assert_users_admin_perm("admin.users.settings")
        return list_active_sessions(self._settings(), username=username)

    def ensure_active_session_registered(self) -> None:
        """Verify this session without recreating a remotely revoked token."""
        if getattr(self, '_session_verification_pending', False):
            raise PermissionError("Vérification de session en attente du rétablissement de la connexion.")
        if not self.is_enforced() or self._session is None:
            return
        session = self._session
        tok = (session.session_token or "").strip()
        from sme_gend.services.auth_session_registry import heartbeat_session

        if tok and heartbeat_session(
            self._settings(),
            session_token=tok,
            username=session.username,
            session_rev=session.session_rev,
            expires_at=session.expires_at,
        ):
            return
        self._session = None
        raise PermissionError("Session non enregistrée, révoquée ou expirée : reconnectez-vous.")

    def local_session_context(self) -> tuple[str, str, str]:
        """(session_token, client_instance_id, machine_label) pour le poste courant."""
        from sme_gend.services.auth_client_instance import client_instance_id, machine_label

        tok = ""
        if self._session is not None:
            tok = (self._session.session_token or "").strip()
        return tok, client_instance_id(), machine_label()

    @atomic_auth_change
    def revoke_active_session(self, session_token: str) -> None:
        from sme_gend.services.auth_session_registry import unregister_session, list_active_sessions

        self._assert_users_admin_perm("admin.users.settings")
        tok = (session_token or "").strip()
        if not tok:
            raise ValueError("Session invalide.")
        if self._session is not None and tok == self._session.session_token:
            raise ValueError("Utilisez la déconnexion pour fermer votre session actuelle.")
        row = next((r for r in list_active_sessions(self._settings()) if r.session_token == tok), None)
        if row is None:
            raise ValueError("Session introuvable ou déjà clôturée. Actualisez la liste.")
        user = self._user_by_name(row.username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        unregister_session(self._settings(), tok)
        self._audit(action="session_revoked", detail=f"{row.username} — poste {row.machine_label}")

    @atomic_auth_change
    def revoke_all_sessions_for_user(self, username: str) -> int:
        from sme_gend.services.auth_session_registry import revoke_user_sessions

        self._assert_users_admin_perm("admin.users.accounts")
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        n = revoke_user_sessions(self._settings(), user.username)
        user.session_rev = int(user.session_rev or 0) + 1
        save_auth_config(self._config)
        self._audit(action="sessions_revoked_all", detail=user.username)
        session = self._session
        if session is not None and session.username.casefold() == user.username.casefold():
            self._session = None
        return n

    def set_enabled(self, enabled: bool) -> None:
        self.save_auth_settings(
            enabled=enabled,
            session_timeout_minutes=self._config.session_timeout_minutes,
        )

    @atomic_auth_change
    def ensure_default_admin(self, password: str) -> AuthUser:
        self.reload_config()
        # Amorçage uniquement lorsque la base ne contient encore aucun compte.
        if self.has_users():
            raise ValueError("Des utilisateurs existent déjà.")
        self._config.ensure_roles_and_catalog()
        validate_password(password, allow_default=True)
        pwd_hash, salt = hash_new_password(password)
        now = _utc_now_iso()
        user = AuthUser(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=pwd_hash,
            salt=salt,
            display_name="Administrateur",
            role=ROLE_SUPER_ADMIN,
            created_at=now,
            password_changed_at=now,
        )
        self._config.users.append(user)
        save_auth_config(self._config)
        self._audit(action="user_created",detail=f"{user.username} (bootstrap)")
        return user

    def bootstrap_default_admin_account(self) -> bool:
        """
        Crée le compte ``admin`` (mot de passe DEFAULT_ADMIN_PASSWORD) s'il n'existe aucun
        utilisateur valide. Ne réinitialise pas le mot de passe à chaque démarrage
        (utiliser scripts/reset_admin_password.py si besoin).
        """
        self.reload_config()
        self._config.users = [
            u
            for u in self._config.users
            if (u.username or "").strip() and u.password_hash and u.salt
        ]
        if self.has_users():
            return False
        self.ensure_default_admin(DEFAULT_ADMIN_PASSWORD)
        self.reload_config()
        return True

    @atomic_auth_change
    def add_user(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "",
        role: str = "operateur",
    ) -> AuthUser:
        # Recharge depuis MySQL avant de muter/sauvegarder : sans ça, deux admins sur
        # deux postes sauvegardaient chacun leur propre copie en mémoire de tout le
        # blob users+roles, et le second écrasait silencieusement les changements du
        # premier (lost update, ex. un utilisateur ajouté par l'un disparaît).
        self.reload_config()
        uname = (username or "").strip()
        if not uname:
            raise ValueError("Identifiant requis.")
        if not _USERNAME_RE.match(uname):
            raise ValueError(
                "Identifiant invalide (lettres, chiffres, . _ - ; 2–32 car., commence par une lettre)."
            )
        self._assert_users_admin_perm("admin.users.accounts")
        validate_password(password)
        if any(u.username.casefold() == uname.casefold() for u in self._config.users):
            raise ValueError(f"L'utilisateur « {uname} » existe déjà.")
        role_id = (role or "operateur").strip()
        self._ensure_role_exists(role_id)
        self._require_can_assign_role(role_id)
        pwd_hash, salt = hash_new_password(password)
        now = _utc_now_iso()
        user = AuthUser(
            username=uname,
            password_hash=pwd_hash,
            salt=salt,
            display_name=(display_name or uname).strip(),
            role=role_id,
            created_at=now,
            password_changed_at=now,
        )
        self._config.users.append(user)
        save_auth_config(self._config)
        self._audit(action="user_created", detail=f"{uname} ({role_id})")
        return user

    @atomic_auth_change
    def update_user(
        self,
        username: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        disabled: bool | None = None,
        expected_profile: tuple[str, str, bool] | None = None,
    ) -> None:
        self._assert_users_admin_perm("admin.users.accounts")
        self.reload_config()
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        current_profile = (user.display_name or user.username, user.role, bool(user.disabled))
        if expected_profile is not None and tuple(expected_profile) != current_profile:
            raise ValueError("Ce compte a été modifié sur un autre poste. Actualisez avant d'enregistrer ; votre saisie est conservée.")
        changed = False
        if role is not None:
            role_id = role.strip()
            self._ensure_role_exists(role_id)
            self._require_can_assign_role(role_id)
            self._assert_not_last_super_admin(user, new_role=role_id)
            if user.role != role_id:
                user.role = role_id
                changed = True
        if display_name is not None:
            new_name = (display_name or user.username).strip()
            if user.display_name != new_name:
                user.display_name = new_name
                changed = True
        if disabled is not None:
            actor = self._session_actor_username()
            if disabled and actor and actor.casefold() == user.username.casefold():
                raise ValueError("Vous ne pouvez pas désactiver votre propre compte.")
            if disabled:
                self._assert_not_last_super_admin(user, new_role=None)
            if bool(user.disabled) != bool(disabled):
                user.disabled = bool(disabled)
                changed = True
        if changed:
            self._bump_user_session_rev(user)
        save_auth_config(self._config)
        self._audit(action="user_updated", detail=user.username)
        session = self._session
        if session is not None and session.username.casefold() == user.username.casefold():
            self._session = AuthSession(
                username=user.username,
                display_name=user.display_name or user.username,
                role=user.role,
                expires_at=session.expires_at,
                must_change_password=session.must_change_password,
                session_rev=user.session_rev,
                roles_rev=self._config.roles_rev,
                session_token=session.session_token,
            )

    @atomic_auth_change
    def rename_user(self, username: str, new_username: str) -> None:
        self._assert_users_admin_perm("admin.users.accounts")
        self.reload_config()
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        actor = self._session_actor_username()
        if actor and actor.casefold() == user.username.casefold():
            raise ValueError("Renommez votre compte via un autre administrateur.")
        new_name = (new_username or "").strip()
        if not new_name:
            raise ValueError("Nouvel identifiant requis.")
        if not _USERNAME_RE.match(new_name):
            raise ValueError(
                "Identifiant invalide (lettres, chiffres, . _ - ; 2–32 car., commence par une lettre)."
            )
        if new_name.casefold() == user.username.casefold():
            return
        if self._user_by_name(new_name) is not None:
            raise ValueError(f"L'identifiant « {new_name} » est déjà utilisé.")
        old_name = user.username
        user.username = new_name
        self._bump_user_session_rev(user)
        save_auth_config(self._config)
        self._audit(action="user_renamed", detail=f"{old_name} → {new_name}")

    def update_user_role(self, username: str, role_id: str) -> None:
        self.update_user(username, role=role_id)

    def update_user_display_name(self, username: str, display_name: str) -> None:
        self.update_user(username, display_name=display_name)

    def change_password(self, username: str, new_password: str) -> None:
        """Réinitialisation par un administrateur (sans ancien mot de passe)."""
        self._assert_users_admin_perm("admin.users.accounts")
        validate_password(new_password)
        self.reload_config()
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        self._apply_password_change(user, new_password, audit_action="password_changed")

    def change_own_password(self, current_password: str, new_password: str) -> None:
        """Changement par l'utilisateur connecté (ancien mot de passe requis)."""
        session = self.current_session()
        if session is None:
            raise ValueError("Session expirée — reconnectez-vous.")
        validate_password(new_password)
        self.reload_config()
        user = self._user_by_name(session.username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        if not verify_password(current_password or "", user):
            raise ValueError("Mot de passe actuel incorrect.")
        if secrets.compare_digest(current_password, new_password):
            raise ValueError("Le nouveau mot de passe doit être différent de l'actuel.")
        self._apply_password_change(user, new_password, audit_action="password_changed_self")

    @atomic_auth_change
    def _apply_password_change(
        self,
        user: AuthUser,
        new_password: str,
        *,
        audit_action: str,
    ) -> None:
        pwd_hash, salt = hash_new_password(new_password)
        user.password_hash = pwd_hash
        user.salt = salt
        user.failed_attempts = 0
        user.locked_until = ""
        user.password_changed_at = _utc_now_iso()
        self._bump_user_session_rev(user)
        save_auth_config(self._config)
        if self._session is not None and self._session.username == user.username:
            prev = self._session
            self._session = AuthSession(
                username=prev.username,
                display_name=user.display_name or user.username,
                role=user.role,
                expires_at=prev.expires_at,
                must_change_password=False,
                session_rev=user.session_rev,
                roles_rev=self._config.roles_rev,
                session_token=prev.session_token,
            )
        self._audit(action=audit_action, detail=user.username)

    @atomic_auth_change
    def unlock_user(self, username: str) -> None:
        self._assert_users_admin_perm("admin.users.accounts")
        self.reload_config()
        user = self._user_by_name(username)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        user.failed_attempts = 0
        user.locked_until = ""
        save_auth_config(self._config)
        self._audit(action="user_unlocked", detail=user.username)

    @atomic_auth_change
    def remove_user(self, username: str) -> None:
        self._assert_users_admin_perm("admin.users.delete")
        self.reload_config()
        uname = (username or "").strip()
        user = self._user_by_name(uname)
        if user is None:
            raise ValueError("Utilisateur introuvable.")
        self._assert_can_modify_user(user)
        actor_name = self._session_actor_username()
        if actor_name and actor_name.casefold() == uname.casefold():
            raise ValueError("Vous ne pouvez pas supprimer le compte de la session en cours.")
        self._assert_not_last_super_admin(user, new_role=None)
        if len(self._config.users) <= 1:
            raise ValueError("Impossible de supprimer le dernier utilisateur.")
        self._config.users = [
            u for u in self._config.users if u.username.casefold() != uname.casefold()
        ]
        save_auth_config(self._config)
        self._audit(action="user_deleted", detail=uname)

    @atomic_auth_change
    def add_role(
        self,
        role_id: str,
        name: str,
        *,
        description: str = "",
        permissions: list[str] | None = None,
    ) -> AuthRole:
        self._assert_users_admin_perm("admin.users.roles")
        self.reload_config()
        rid = self._validate_role_id(role_id)
        if self._config.role_by_id(rid):
            raise ValueError(f"Le rôle « {rid} » existe déjà.")
        role = AuthRole(
            id=rid,
            name=(name or rid).strip(),
            description=(description or "").strip(),
            permissions=sanitize_role_permissions(rid, list(permissions or [])),
        )
        self._config.roles.append(role)
        self._bump_roles_rev()
        save_auth_config(self._config)
        self._audit(action="role_created", detail=rid)
        return role

    @atomic_auth_change
    def update_role(
        self,
        role_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        permissions: list[str] | None = None,
        expected_profile: tuple[str, str, tuple[str, ...]] | None = None,
    ) -> AuthRole:
        self._assert_users_admin_perm("admin.users.roles")
        self.reload_config()
        role = self._config.role_by_id(role_id)
        if role is None:
            raise ValueError("Rôle introuvable.")
        if is_super_admin_role(role_id):
            raise ValueError("Le rôle super administrateur ne peut pas être modifié.")
        current_profile = (role.name, role.description, tuple(sorted(role.permissions)))
        if expected_profile is not None and expected_profile != current_profile:
            raise ValueError("Ce rôle a été modifié sur un autre poste. Actualisez avant d'enregistrer ; votre saisie est conservée.")
        if name is not None:
            role.name = name.strip() or role.id
        if description is not None:
            role.description = description.strip()
        if permissions is not None:
            role.permissions = sanitize_role_permissions(role_id, list(permissions))
        self._bump_roles_rev()
        save_auth_config(self._config)
        self._audit(action="role_updated", detail=role_id)
        return role

    @atomic_auth_change
    def remove_role(self, role_id: str) -> None:
        self._assert_users_admin_perm("admin.users.roles")
        self.reload_config()
        rid = (role_id or "").strip()
        if is_system_protected_role(rid):
            raise ValueError("Ce rôle système ne peut pas être supprimé.")
        users_with_role = [u for u in self._config.users if u.role == rid]
        if users_with_role:
            names = ", ".join(u.username for u in users_with_role[:5])
            raise ValueError(
                f"Des utilisateurs utilisent encore ce rôle : {names}"
                + ("…" if len(users_with_role) > 5 else "")
            )
        before = len(self._config.roles)
        self._config.roles = [r for r in self._config.roles if r.id != rid]
        if len(self._config.roles) == before:
            raise ValueError("Rôle introuvable.")
        self._bump_roles_rev()
        save_auth_config(self._config)
        self._audit(action="role_deleted", detail=rid)

    @atomic_auth_change
    def add_custom_permission(self, perm: PermissionDef) -> None:
        self.reload_config()
        self._assert_users_admin_perm("admin.users.roles")
        catalog = self._config.permission_catalog
        catalog = [c for c in catalog if c.get("id") != perm.id]
        catalog.append(perm.to_dict())
        self._config.permission_catalog = catalog
        save_auth_config(self._config)

        self._audit(action="permission_updated",detail=perm.id)
