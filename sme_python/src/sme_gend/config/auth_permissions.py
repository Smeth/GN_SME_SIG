"""Catalogue des permissions applicatives (extensible sans modifier le code métier)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ── Rôles système ─────────────────────────────────────────────────────────────

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_OPERATEUR = "operateur"
ROLE_LECTEUR = "lecteur"

# Rôles non supprimables ; seuls admin et super_admin ont des permissions figées.
SYSTEM_PROTECTED_ROLES: frozenset[str] = frozenset(
    {ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATEUR, ROLE_LECTEUR}
)

# Permissions réservées admin (+ super_admin via wildcard *)
ADMIN_USERS_SUBPERMS: tuple[str, ...] = (
    "admin.users.view",
    "admin.users.accounts",
    "admin.users.roles",
    "admin.users.settings",
    "admin.users.delete",
    "admin.users.journal",
)

ADMIN_USERS_ALL: frozenset[str] = frozenset({"admin.users", *ADMIN_USERS_SUBPERMS})

ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset(
    {
        "attestations.doc_log",
        "admin.sensitive_audit",
        "admin.edit_age",
        "admin.users",
        "effectifs.restore",
        "effectifs.history_correction",
        "workflow.reopen_month",
        "workflow.resolve_task",
        "workflow.cycle_mode",
        "workflow.correct_movement",
        "publications.validate",
        "publications.replace",
        "avancement.regles",
        "system.restore",
        "system.migrate",
    }
)


@dataclass(frozen=True, slots=True)
class PermissionDef:
    id: str
    label: str
    group: str
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PermissionDef | None:
        pid = str(d.get("id", "")).strip()
        if not pid:
            return None
        return cls(
            id=pid,
            label=str(d.get("label", pid)).strip() or pid,
            group=str(d.get("group", "Autre")).strip() or "Autre",
            description=str(d.get("description", "")).strip(),
        )


def default_permission_catalog() -> tuple[PermissionDef, ...]:
    """Liste de référence : peut être complétée dans auth_config.json."""
    rows = [
        ("dashboard.view", "Tableau de bord", "Consulter", ""),
        ("effectifs.liste", "Liste générale", "Consulter", ""),
        ("structures.effectifs", "Effectifs par structure", "Consulter", ""),
        ("effectifs.edition", "Édition SME", "Production SME", ""),
        (
            "effectifs.delete",
            "Supprimer des fiches personnel",
            "Production SME",
            "Retire une ligne de carrière du mois courant (et l'agent si dernier segment). "
            "Droit distinct de l'édition — assignable par rôle.",
        ),
        (
            "effectifs.restore",
            "Restaurer des fiches retirées de la liste",
            "Administration et outils",
            "Consultation et restauration depuis Paramètres (journal d'audit personnel).",
        ),
        (
            "effectifs.sme_dpf",
            "SME DPF",
            "Production SME",
            "Édition et export du personnel DPF (structures DPF, CS-DPF, COMGEND lié DPF)",
        ),
        ("effectifs.presents", "Liste des présents", "Production SME", ""),
        ("effectifs.layout", "Mise en page (présents)", "Production SME", ""),
        ("situation.numerique", "Situation numérique", "Production SME", ""),
        ("detaches.view", "Détachés", "Production SME", ""),
        ("situation.gain_perte", "Gains & pertes", "Suivi administratif et activités", ""),
        ("situation.malade_punis", "Malades & punis", "Suivi administratif et activités", ""),
        ("situation.stages", "Stages", "Suivi administratif et activités", ""),
        ("situation.missions", "Missions", "Suivi administratif et activités", ""),
        ("situation.desertion", "Désertions", "Suivi administratif et activités", ""),
        ("situation.conges", "Congés", "Suivi administratif et activités", ""),
        ("workflow.pilotage", "Cycle mensuel", "Piloter le mois", "Onglet Cycle mensuel de Gestion des mois et archives."),
        ("workflow.correct_movement", "Corriger les mouvements datés", "Piloter le mois",
         "Administrateur : corriger ou supprimer un gain ou une perte datée, et déclarer un mouvement historique avec motif."),
        ("system.archives", "Fichiers d’archive", "Piloter le mois", ""),
        (
            "attestations.doc_log",
            "Historique des documents (complet)",
            "Attestations",
            "Journal complet + purge — administrateur uniquement.",
        ),
        (
            "attestations.history",
            "Historique attestations (consultation)",
            "Attestations",
            "Consultation des attestations limite d'âge et soit transmis (lecture seule).",
        ),
        ("attestations.limite_age", "Attestations et suivi de la retraite", "Attestations", ""),
        (
            "avancement.view",
            "Avancement au grade",
            "Avancement",
            "Consultation de la vigilance, du tableau et des règles.",
        ),
        (
            "avancement.gerer",
            "Gestion des campagnes (retirée)",
            "Avancement",
            "Aucune action disponible. Cette autorisation n'active ni campagnes, ni quotas, ni nominations.",
        ),
        (
            "avancement.regles",
            "Modifier les règles d'avancement",
            "Avancement",
            "Ajuster le barème du décret. Réservé aux administrateurs.",
        ),
        ("attestations.soit_transmis", "Soit transmis", "Attestations", ""),
        (
            "admin.sensitive_audit",
            "Audit données sensibles",
            "Administration et outils",
            "Journal des actions sur identité et historique carrière — admin uniquement.",
        ),
        (
            "admin.edit_age",
            "Modifier l'âge / date de naissance",
            "Administration",
            "Donnée très sensible — réservé administrateur et super administrateur ; "
            "chaque modification est tracée dans l'audit.",
        ),
        ("structures.codes", "Structures & subdivisions", "Administration et outils", ""),
        ("referentiels.grades", "Grades", "Administration et outils", ""),
        ("referentiels.fonctions", "Fonctions", "Administration et outils", ""),
        ("referentiels.padmin", "Positions administratives", "Administration et outils", ""),
        (
            "referentiels.distinctions",
            "Médailles & décorations",
            "Administration et outils",
            "Référentiels et ordre de préséance des distinctions honorifiques.",
        ),
        ("detaches.config", "Configuration Excel détachés", "Administration et outils", ""),
        ("system.settings", "Paramètres & données", "Administration et outils", ""),
        ("system.backup", "Créer et consulter des sauvegardes", "Administration et outils", "N'autorise pas la restauration."),
        ("system.restore", "Restaurer, vider et réinitialiser les données", "Administration et outils", "Remplace ou supprime des données avec sauvegarde préalable et maintenance. Réservé aux administrateurs."),
        ("system.migrate", "Mettre à niveau le schéma MySQL", "Administration et outils", "Migrations explicites contrôlées, réservées aux administrateurs."),
        ("system.import", "Importer des mises à jour CSV (Édition SME)", "Administration et outils", ""),
        ("admin.users", "Utilisateurs & droits (accès complet)", "Administration", ""),
        (
            "admin.users.view",
            "Utilisateurs — consultation",
            "Administration",
            "Voir les comptes et le journal (lecture seule).",
        ),
        (
            "admin.users.accounts",
            "Utilisateurs — comptes",
            "Administration",
            "Créer, modifier, verrouiller, désactiver ou renommer des comptes.",
        ),
        (
            "admin.users.roles",
            "Utilisateurs — rôles",
            "Administration",
            "Gérer les rôles et leurs permissions.",
        ),
        (
            "admin.users.settings",
            "Utilisateurs — connexion",
            "Administration",
            "Activer l'authentification, régler session / verrouillage, consulter les sessions actives et les déconnecter à distance.",
        ),
        (
            "admin.users.delete",
            "Utilisateurs — suppression",
            "Administration",
            "Supprimer des comptes.",
        ),
        (
            "admin.users.journal",
            "Utilisateurs — journal",
            "Administration",
            "Consulter, filtrer et exporter le journal d'authentification.",
        ),
        ("workflow.set_active_month", "Changer le mois actif", "Workflow", ""),
        ("workflow.close_month", "Clôturer un mois", "Workflow", ""),
        ("workflow.reopen_month", "Réouvrir un mois (exception)", "Workflow", ""),
        ("workflow.import_archive", "Importer une archive", "Workflow", ""),
        ("workflow.resolve_task", "Traiter les tâches workflow", "Workflow", ""),
        ("workflow.cycle_mode", "Activer le cycle documentaire", "Workflow", "Choix explicite du mode de clôture pour cette base, réservé aux administrateurs."),
        ("publications.view", "Consulter les versions SME", "Publications SME", "Registre partagé des brouillons, validations et remplacements."),
        ("publications.create", "Créer une version SME", "Publications SME", "Enregistrer un nouveau classeur figé dans le registre partagé."),
        ("publications.verify", "Vérifier une version SME", "Publications SME", "Contrôler l’intégrité des fichiers conservés."),
        ("publications.validate", "Valider une version SME", "Publications SME", "Désigner la version validée du mois ; réservé aux administrateurs."),
        ("publications.replace", "Remplacer une version validée", "Publications SME", "Nouvelle validation avec motif, en conservant la version précédente ; réservé aux administrateurs."),
        ("publications.download", "Télécharger une version SME", "Publications SME", "Copier le classeur conservé ; le droit d’export des effectifs est également requis."),
        ("effectifs.history_correction", "Corriger un retour d’activité validé", "Administration", "Correction datée et motivée de l’historique des retours ; réservée aux administrateurs."),
        ("effectifs.export", "Exporter et imprimer listes / documents", "Production SME", "Exports de fichiers, aperçus d'impression et impression des listes et documents métier."),
    ]
    return tuple(PermissionDef(id=r[0], label=r[1], group=r[2], description=r[3]) for r in rows)


def all_permission_ids() -> list[str]:
    return [p.id for p in default_permission_catalog()]


def admin_role_permissions() -> list[str]:
    """Administrateur : tout le métier + sections sensibles (sauf wildcard *)."""
    return list(all_permission_ids())


def operateur_role_permissions() -> list[str]:
    """Opérateur : saisie métier + historique attestations, sans administration."""
    blocked = ADMIN_ONLY_PERMISSIONS | ADMIN_USERS_ALL | {"system.backup"}
    return [p for p in all_permission_ids() if p not in blocked]


def users_admin_page_permissions() -> tuple[str, ...]:
    """Permissions ouvrant la page Utilisateurs & droits."""
    return ("admin.users", *ADMIN_USERS_SUBPERMS)


def lecteur_role_permissions() -> list[str]:
    """Lecteur : consultation du personnel et de la situation numérique, sans écriture."""
    return [
        "dashboard.view",
        "effectifs.liste",
        "structures.effectifs",
        "situation.numerique",
        "avancement.view",
        "publications.view",
        "publications.verify",
    ]


def merge_permission_catalog(
    stored: list[dict[str, Any]] | None,
) -> list[PermissionDef]:
    """Fusionne le catalogue par défaut avec les entrées personnalisées du fichier config."""
    by_id: dict[str, PermissionDef] = {p.id: p for p in default_permission_catalog()}
    if isinstance(stored, list):
        for raw in stored:
            if isinstance(raw, dict):
                p = PermissionDef.from_dict(raw)
                if p:
                    by_id[p.id] = p
    groups_order = []
    for p in by_id.values():
        if p.group not in groups_order:
            groups_order.append(p.group)
    group_rank = {g: i for i, g in enumerate(groups_order)}
    return sorted(
        by_id.values(),
        key=lambda p: (group_rank.get(p.group, 999), p.group, p.label),
    )


def default_roles_seed() -> list[dict[str, Any]]:
    return [
        {
            "id": ROLE_SUPER_ADMIN,
            "name": "Super administrateur",
            "description": "Accès total ; rôle système protégé (non modifiable)",
            "permissions": ["*"],
        },
        {
            "id": ROLE_ADMIN,
            "name": "Administrateur",
            "description": "Administration complète (utilisateurs, audit, réouverture de mois)",
            "permissions": admin_role_permissions(),
        },
        {
            "id": ROLE_OPERATEUR,
            "name": "Opérateur",
            "description": "Saisie et production SME, historique attestations (sans administration)",
            "permissions": operateur_role_permissions(),
        },
        {
            "id": ROLE_LECTEUR,
            "name": "Lecteur",
            "description": "Consultation du personnel et de la situation numérique (sans édition ni configuration)",
            "permissions": lecteur_role_permissions(),
        },
    ]


def canonical_permissions_for_role(role_id: str) -> list[str] | None:
    """Permissions figées d'un rôle système, ou None pour un rôle personnalisé."""
    rid = (role_id or "").strip()
    if rid == ROLE_SUPER_ADMIN:
        return ["*"]
    if rid == ROLE_ADMIN:
        return admin_role_permissions()
    if rid == ROLE_OPERATEUR:
        return operateur_role_permissions()
    if rid == ROLE_LECTEUR:
        return lecteur_role_permissions()
    return None


def sanitize_role_permissions(role_id: str, permissions: list[str]) -> list[str]:
    """Applique les règles système (profils figés, wildcard, permissions admin-only)."""
    rid = (role_id or "").strip()
    if rid == ROLE_SUPER_ADMIN:
        return ["*"]
    if rid == ROLE_ADMIN:
        return list(admin_role_permissions())
    perms = [p for p in permissions if p and str(p).strip()]
    if "*" in perms:
        raise ValueError("Le joker « * » est réservé au super administrateur.")
    blocked = ADMIN_ONLY_PERMISSIONS | frozenset({"admin.users"})
    return [p for p in perms if p not in blocked]


def role_may_grant_permission(role_id: str, permission_id: str) -> bool:
    """Indique si un rôle peut recevoir une permission (UI + validation)."""
    rid = (role_id or "").strip()
    if rid == ROLE_SUPER_ADMIN:
        return True
    if rid == ROLE_ADMIN:
        return permission_id in admin_role_permissions()
    if permission_id in ADMIN_ONLY_PERMISSIONS or permission_id == "admin.users":
        return False
    return permission_id != "*"


def role_permissions_locked(role_id: str) -> bool:
    """True si l'UI ne doit pas laisser modifier les cases à cocher du rôle."""
    return (role_id or "").strip() in (ROLE_SUPER_ADMIN, ROLE_ADMIN)


def is_system_protected_role(role_id: str) -> bool:
    return (role_id or "").strip() in SYSTEM_PROTECTED_ROLES


def is_super_admin_role(role_id: str) -> bool:
    return (role_id or "").strip() == ROLE_SUPER_ADMIN


def is_admin_rank_role(role_id: str) -> bool:
    """Super administrateur ou administrateur (actions d'identité / audit)."""
    return (role_id or "").strip() in (ROLE_SUPER_ADMIN, ROLE_ADMIN)


def reconcile_system_roles(roles: list[Any]) -> bool:
    """
    Aligne les rôles système sur le modèle canonique (migration progressive).
    Retourne True si la config a été modifiée.
    """
    from sme_gend.config.auth_config import AuthRole

    changed = False
    by_id: dict[str, AuthRole] = {r.id: r for r in roles if hasattr(r, "id")}
    seed_by_id = {r["id"]: r for r in default_roles_seed()}

    insert_at = 0
    for rid in (ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_OPERATEUR, ROLE_LECTEUR):
        if rid not in by_id:
            raw = seed_by_id.get(rid)
            role = AuthRole.from_dict(raw) if raw else None
            if role:
                roles.insert(insert_at, role)
                by_id[rid] = role
                changed = True
        insert_at += 1

    for rid in (ROLE_SUPER_ADMIN, ROLE_ADMIN):
        role = by_id.get(rid)
        expected = canonical_permissions_for_role(rid)
        if role is None or expected is None:
            continue
        if list(role.permissions) != list(expected):
            role.permissions = list(expected)
            changed = True

    for rid in (ROLE_OPERATEUR, ROLE_LECTEUR):
        role = by_id.get(rid)
        if role is None:
            continue
        # Les rôles existants conservent les choix enregistrés par l'admin.
        # Les nouveaux rôles reçoivent déjà les droits par défaut à leur création.
        cleaned = sanitize_role_permissions(rid, list(role.permissions))
        if list(role.permissions) != cleaned:
            role.permissions = cleaned
            changed = True

    for role in roles:
        if role.id in SYSTEM_PROTECTED_ROLES:
            continue
        stripped = [p for p in role.permissions if p not in ADMIN_ONLY_PERMISSIONS and p != "*"]
        if stripped != list(role.permissions):
            role.permissions = stripped
            changed = True

    return changed


def reconcile_system_users(users: list[Any], *, default_admin_username: str = "admin",
                           legacy_bootstrap: bool = False) -> bool:
    """Migration explicite de l'ancien compte initial, jamais une règle de nommage."""
    if not legacy_bootstrap or any(getattr(u, "role", "") == ROLE_SUPER_ADMIN for u in users):
        return False
    changed = False
    key = default_admin_username.casefold()
    for u in users:
        if not hasattr(u, "username"):
            continue
        if u.username.casefold() == key and getattr(u, "role", "") == ROLE_ADMIN:
            u.role = ROLE_SUPER_ADMIN
            changed = True
    return changed
