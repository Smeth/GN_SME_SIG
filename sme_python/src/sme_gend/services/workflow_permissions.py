"""Permissions granulaires sur les actions workflow (hors navigation par page)."""

from __future__ import annotations

# action_id → permission catalogue
WORKFLOW_ACTION_PERMISSION: dict[str, str] = {
    "workflow.set_active_month": "workflow.set_active_month",
    "workflow.close_month": "workflow.close_month",
    "workflow.reopen_month": "workflow.reopen_month",
    "workflow.import_archive": "workflow.import_archive",
    "workflow.fill_month": "workflow.import_archive",
    "workflow.resolve_task": "workflow.resolve_task",
    "effectifs.export": "effectifs.export",
    "system.import_data": "system.import",
    "system.backup": "system.backup",
    "system.restore": "system.restore",
}


def session_can_perform_action(action_id: str) -> bool:
    from sme_gend.services.auth_context import get_app_auth

    auth = get_app_auth()
    if auth is None:
        return True
    perm = WORKFLOW_ACTION_PERMISSION.get(action_id)
    if not perm:
        # Refus par défaut (fail-closed) : tout action_id non enregistré ci-dessus
        # est bloqué plutôt qu'autorisé silencieusement. Ajoutez-le explicitement
        # à WORKFLOW_ACTION_PERMISSION pour le rendre utilisable.
        return False
    return auth.session_has_permission(perm)
