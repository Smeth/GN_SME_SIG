"""Champs et mutations d'historique réservés aux administrateurs."""

from __future__ import annotations

from datetime import date, datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.dossier_provenance import OBS_IMPORT_PREFIX

# Identité (hors date de naissance) — éditable opérateur avec effectifs.edition / SME DPF.
IDENTITY_PERSONNEL_FIELDS: frozenset[str] = frozenset(
    {
        "noms",
        "lieu_naissance",
        "sexe",
        "telephone",
        "adresse_residence",
        "matricule_militaire",
        "matricule_solde",
        "ordre_tri",
        "presseance",
        "access_join_id",
    }
)

# Rétrocompatibilité interne (alias).
IDENTITY_ONLY_PERSONNEL_FIELDS = IDENTITY_PERSONNEL_FIELDS

# Date de naissance / âge — permission dédiée ``admin.edit_age`` (administrateurs).
AGE_SENSITIVE_PERSONNEL_FIELDS: frozenset[str] = frozenset({"date_naissance"})

# Carrière (dates, diplôme) — éditables avec la permission effectifs.edition.
CAREER_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "date_grade",
        "date_service",
        "ndecision",
        "diplome",
        "date_obtention",
    }
)

ADMIN_ONLY_PERSONNEL_FIELDS: frozenset[str] = (
    IDENTITY_PERSONNEL_FIELDS
    | AGE_SENSITIVE_PERSONNEL_FIELDS
    | CAREER_SENSITIVE_FIELDS
)

SENSITIVE_FIELD_LABELS_FR: dict[str, str] = {
    "noms": "Nom et prénoms",
    "date_naissance": "Date de naissance",
    "lieu_naissance": "Lieu de naissance",
    "sexe": "Sexe",
    "telephone": "Téléphone",
    "adresse_residence": "Adresse de résidence",
    "matricule_militaire": "Matricule militaire",
    "matricule_solde": "Matricule solde",
    "date_grade": "Date de grade",
    "date_service": "Date d'entrée en service",
    "ndecision": "N° décision",
    "diplome": "Diplôme (liste)",
    "date_obtention": "Date d'obtention diplôme",
    "ordre_tri": "Ordre de tri",
    "presseance": "Préséance",
    "access_join_id": "Identifiant liaison Access",
}


class SensitiveDataPermissionError(PermissionError):
    """Le compte courant ne peut pas modifier ces données."""


def session_may_edit_sensitive_data() -> bool:
    from sme_gend.services.auth_guard import session_has_admin_role

    return session_has_admin_role()


def session_may_edit_age_data() -> bool:
    from sme_gend.services.auth_guard import session_may_edit_age

    return session_may_edit_age()


def session_may_edit_personnel_identity() -> bool:
    """Nom, matricules, sexe… — opérateur avec édition SME / DPF ou administrateur."""
    from sme_gend.services.auth_guard import (
        session_can_modify_personnel,
        session_has_admin_role,
    )

    return session_has_admin_role() or session_can_modify_personnel()


def session_may_edit_career_fields() -> bool:
    """Dates de carrière et diplôme : admin ou opérateur avec édition SME / DPF."""
    from sme_gend.services.auth_guard import (
        session_can_edit_effectifs,
        session_can_edit_sme_dpf,
        session_has_admin_role,
    )

    return (
        session_has_admin_role()
        or session_can_edit_effectifs()
        or session_can_edit_sme_dpf()
    )


def is_imported_dossier_entry(obs: str | None) -> bool:
    t = (obs or "").strip()
    return bool(t and t.startswith(OBS_IMPORT_PREFIX))


def sensitive_field_changes(
    before: ListeGeneraleRow,
    after: ListeGeneraleRow,
) -> dict[str, dict]:
    from sme_gend.services.audit_log import diff_personnel_rows

    changes = diff_personnel_rows(before, after)
    return {k: v for k, v in changes.items() if k in ADMIN_ONLY_PERSONNEL_FIELDS}


def assert_may_edit_personnel_sensitive_fields(
    before: ListeGeneraleRow,
    after: ListeGeneraleRow,
) -> None:
    """Lève si le compte tente de modifier des champs réservés."""
    blocked = sensitive_field_changes(before, after)
    if session_may_edit_career_fields():
        blocked = {
            k: v for k, v in blocked.items() if k not in CAREER_SENSITIVE_FIELDS
        }
    age_blocked = {
        k: v for k, v in blocked.items() if k in AGE_SENSITIVE_PERSONNEL_FIELDS
    }
    identity_blocked = {
        k: v for k, v in blocked.items() if k in IDENTITY_PERSONNEL_FIELDS
    }
    if age_blocked and not session_may_edit_age_data():
        labels = ", ".join(
            SENSITIVE_FIELD_LABELS_FR.get(k, k) for k in sorted(age_blocked.keys())
        )
        raise SensitiveDataPermissionError(
            f"Modification refusée — {labels} : permission « Modifier l'âge / date de "
            "naissance » réservée aux administrateurs."
        )
    if identity_blocked and not session_may_edit_personnel_identity():
        labels = ", ".join(
            SENSITIVE_FIELD_LABELS_FR.get(k, k) for k in sorted(identity_blocked.keys())
        )
        raise SensitiveDataPermissionError(
            f"Modification refusée — droits d'édition du personnel requis : {labels}."
        )


def assert_may_edit_personnel(*, action: str = "modifier le personnel") -> None:
    if session_may_edit_personnel_identity():
        return
    raise SensitiveDataPermissionError(
        f"Action « {action} » : droits d'édition SME ou du personnel requis."
    )


def assert_may_add_dated_event(*, action: str = "ajouter un événement") -> None:
    assert_may_edit_personnel(action=action)


def assert_may_add_diplome(*, action: str = "ajouter un diplôme") -> None:
    """An editor may record a new qualification without editing old history."""
    if session_may_edit_career_fields():
        return
    raise SensitiveDataPermissionError(
        f"Action « {action} » : droits d'édition SME ou du personnel requis."
    )


def assert_may_mutate_dossier_history(*, action: str) -> None:
    """Passations, affectations et diplômes (historique carrière)."""
    if session_may_edit_sensitive_data():
        return
    raise SensitiveDataPermissionError(
        f"Action « {action} » réservée aux administrateurs et super administrateurs "
        "(historique de carrière et données d'identité)."
    )


def assert_may_mutate_imported_dossier_entry(*, action: str, obs: str | None) -> None:
    if not is_imported_dossier_entry(obs):
        return
    if session_may_edit_sensitive_data():
        return
    raise SensitiveDataPermissionError(
        f"Action « {action} » refusée sur une entrée issue d'un import d'archives "
        "(réservé aux administrateurs)."
    )
