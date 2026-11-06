"""Contrôle d'accès navigation : pages ↔ permissions du catalogue auth."""

from __future__ import annotations

from sme_gend.ui import navigation_pages as nav

# Indice QStackedWidget → permission requise (None = toujours accessible si session OK)
PAGE_PERMISSION: dict[int, str | None] = {
    nav.PAGE_CAREER_ALERTS: "effectifs.liste",
    nav.PAGE_DASHBOARD: "dashboard.view",
    nav.PAGE_LISTE_GENERALE: "effectifs.liste",
    nav.PAGE_STRUCTURES_EFFECTIFS: "structures.effectifs",
    nav.PAGE_LAYOUT_EDITOR: "effectifs.layout",
    nav.PAGE_LISTE_PRESENTS: "effectifs.presents",
    nav.PAGE_DETACHES: "detaches.view",
    nav.PAGE_STRUCTURES: "structures.codes",
    nav.PAGE_GRADES: "referentiels.grades",
    nav.PAGE_FONCTIONS: "referentiels.fonctions",
    nav.PAGE_DETACHES_CONFIG: "detaches.config",
    nav.PAGE_NUMERIQUE: "situation.numerique",
    nav.PAGE_ARCHIVES_CLOSURE: "system.archives",
    nav.PAGE_SETTINGS: "system.settings",
    nav.PAGE_BACKUP_RESTORE: "system.backup",
    nav.PAGE_GAIN_PERTE: "situation.gain_perte",
    nav.PAGE_DELETED_PERSONNEL: "effectifs.delete",
    nav.PAGE_MALADE_PUNIS: "situation.malade_punis",
    nav.PAGE_STAGES: "situation.stages",
    nav.PAGE_MISSIONS: "situation.missions",
    nav.PAGE_DESERTION: "situation.desertion",
    nav.PAGE_CONGES: "situation.conges",
    nav.PAGE_AVANCEMENT: "avancement.view",
    nav.PAGE_LIMITE_AGE: "attestations.limite_age",
    nav.PAGE_SOIT_TRANSMIS: "attestations.soit_transmis",
    nav.PAGE_DOC_GEN_LOG: "attestations.doc_log",
    nav.PAGE_ACTIVITY_AUDIT: "admin.sensitive_audit",
    nav.PAGE_SENSITIVE_AUDIT: "admin.sensitive_audit",
    nav.PAGE_EDITION_SME: "effectifs.edition",
    nav.PAGE_SME_DPF: "effectifs.sme_dpf",
    nav.PAGE_PADMIN: "referentiels.padmin",
    nav.PAGE_DISTINCTIONS: "referentiels.distinctions",
    nav.PAGE_USERS_AUTH: "admin.users",
    nav.PAGE_PILOTAGE_MOIS: "workflow.pilotage",
}


def permission_for_page(page_index: int) -> str | None:
    return PAGE_PERMISSION.get(page_index)


def session_can_access_page(page_index: int) -> bool:
    """Auth désactivée ou permission « * » → True ; sinon vérifie la session."""
    from sme_gend.services.auth_context import get_app_auth

    if page_index == nav.PAGE_CSV_IMPORT:
        return False  # Retired page, including old saved navigation requests.
    if page_index not in PAGE_PERMISSION:
        return False
    auth = get_app_auth()
    if auth is None:
        return True
    if page_index == nav.PAGE_BACKUP_RESTORE:
        return auth.session_has_permission("system.backup") or auth.session_has_permission("system.restore")
    if page_index == nav.PAGE_DELETED_PERSONNEL:
        return auth.session_has_permission("effectifs.delete") or auth.session_has_permission("effectifs.restore")
    # Page fusionnée : accessible si l'un des deux onglets l'est.
    if page_index in (nav.PAGE_ARCHIVES_CLOSURE, nav.PAGE_PILOTAGE_MOIS):
        return auth.session_has_permission("workflow.pilotage") or auth.session_has_permission(
            "system.archives"
        )
    perm = permission_for_page(page_index)
    if page_index == nav.PAGE_USERS_AUTH:
        from sme_gend.services.auth_guard import session_has_users_admin_access

        return session_has_users_admin_access()
    if page_index == nav.PAGE_DOC_GEN_LOG:
        from sme_gend.services.auth_guard import session_can_view_attestation_history

        return session_can_view_attestation_history()
    if perm is None:
        return True
    return auth.session_has_permission(perm)


def first_accessible_page(fallback: int = nav.PAGE_DASHBOARD) -> int:
    """Première page réellement ouvrable pour la session (alias virtuels exclus)."""
    for idx in range(nav.PAGE_COUNT):
        if idx == nav.PAGE_PILOTAGE_MOIS:
            continue
        if session_can_access_page(idx):
            return idx
    return fallback
