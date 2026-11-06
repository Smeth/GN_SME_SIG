"""Garde-fous auth pour actions UI (écriture, export, sections sensibles)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


def _auth_service():
    from sme_gend.services.auth_context import get_app_auth

    return get_app_auth()


def _session_has(permission_id: str) -> bool:
    auth = _auth_service()
    if auth is None:
        return True
    return auth.session_has_permission(permission_id)


def session_has_permission(permission_id: str) -> bool:
    return _session_has(permission_id)


def session_can_edit_effectifs() -> bool:
    return _session_has("effectifs.edition")


def session_can_edit_sme_dpf() -> bool:
    return _session_has("effectifs.sme_dpf")


def session_can_modify_personnel() -> bool:
    """Édition fiche / liste : Édition SME ou SME DPF."""
    return session_can_edit_effectifs() or session_can_edit_sme_dpf()


def session_can_delete_personnel() -> bool:
    return _session_has("effectifs.delete")


def session_can_restore_personnel() -> bool:
    return _session_has("effectifs.restore")


def session_can_export_documents() -> bool:
    return _session_has("effectifs.export")


def session_can_view_doc_log() -> bool:
    return _session_has("attestations.doc_log")


def session_can_view_attestation_history() -> bool:
    """Journal complet (admin) ou historique attestations seulement (opérateur)."""
    return _session_has("attestations.doc_log") or _session_has("attestations.history")


def session_can_purge_doc_log() -> bool:
    return _session_has("attestations.doc_log")


def session_can_view_sensitive_audit() -> bool:
    return _session_has("admin.sensitive_audit")


def session_may_edit_age() -> bool:
    """Date de naissance / âge — permission ``admin.edit_age`` (administrateurs uniquement)."""
    auth = _auth_service()
    if auth is None or not auth.is_enforced():
        return True
    return _session_has("admin.edit_age")


def session_has_admin_role() -> bool:
    """True si super_admin ou admin. Auth désactivée = mode local, tout autorisé."""
    from sme_gend.config.auth_permissions import is_admin_rank_role

    auth = _auth_service()
    if auth is None or not auth.is_enforced():
        return True
    session = auth.current_session()
    if session is None:
        return False
    return is_admin_rank_role(session.role)


def session_is_super_admin() -> bool:
    """True si super_admin. Auth désactivée = mode local, traité comme super admin."""
    from sme_gend.config.auth_permissions import is_super_admin_role

    auth = _auth_service()
    if auth is None or not auth.is_enforced():
        return True
    session = auth.current_session()
    if session is None:
        return False
    return is_super_admin_role(session.role)


def require_admin_role(
    parent: QWidget | None,
    *,
    title: str = "Droits",
    message: str | None = None,
) -> bool:
    if session_has_admin_role():
        return True
    if parent is None:
        return False
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(
        parent,
        title,
        message
        or "Cette action est réservée aux administrateurs et super administrateurs.",
    )
    return False


def require_permission(
    parent: QWidget | None,
    permission_id: str,
    *,
    title: str = "Droits",
    message: str | None = None,
) -> bool:
    if _session_has(permission_id):
        return True
    if parent is None:
        return False
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.warning(
        parent,
        title,
        message or "Votre compte n'a pas la permission requise pour cette action.",
    )
    return False


def _users_admin_perm(permission_id: str) -> bool:
    if _session_has("admin.users"):
        return True
    return _session_has(permission_id)


def session_has_users_admin_access() -> bool:
    from sme_gend.config.auth_permissions import users_admin_page_permissions

    auth = _auth_service()
    if auth is None or not auth.is_enforced():
        return True
    return any(_session_has(pid) for pid in users_admin_page_permissions())


def session_can_view_user_accounts() -> bool:
    return _users_admin_perm("admin.users.view") or _users_admin_perm("admin.users.accounts")


def session_can_manage_user_accounts() -> bool:
    return _users_admin_perm("admin.users.accounts")


def session_can_delete_user_accounts() -> bool:
    return _users_admin_perm("admin.users.delete")


def session_can_manage_user_roles() -> bool:
    return _users_admin_perm("admin.users.roles")


def session_can_manage_auth_settings() -> bool:
    return _users_admin_perm("admin.users.settings")


def session_can_view_auth_journal() -> bool:
    return _users_admin_perm("admin.users.journal") or _users_admin_perm("admin.users.view")
