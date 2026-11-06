"""Typologie d'unité pour le référentiel structures (direction, région, école…)."""

from __future__ import annotations

# (code stocké MySQL, libellé UI)
STRUCTURE_FAMILLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "— Non renseigné —"),
    ("ETAT_MAJOR", "État major"),
    ("DIRECTION", "Direction"),
    ("REGION", "Région / zone"),
    ("GROUPEMENT", "Groupement"),
    ("CENTRE_INSTRUCTION", "Centre d'instruction"),
    ("CENTRE_ENTRAINEMENT", "Centre d'entraînement"),
    ("ECOLE", "École / institut"),
    ("CABINET", "Cabinet"),
    ("OFFICE", "Office central"),
    ("COMMANDEMENT", "Commandement"),
    ("CRO", "Centre de renseignements"),
    ("INSPECTION", "Inspection technique"),
    ("UNITE", "Unité"),
    ("AUTRE", "Autre"),
)

_FAMILLE_LABEL: dict[str, str] = {code: label for code, label in STRUCTURE_FAMILLE_CHOICES if code}


def structure_famille_label(code: str | None) -> str:
    c = (code or "").strip()
    if not c:
        return "—"
    return _FAMILLE_LABEL.get(c, c.replace("_", " ").title())


def normalize_structure_famille(code: str | None) -> str | None:
    c = (code or "").strip()
    if not c:
        return None
    if c in _FAMILLE_LABEL:
        return c
    return None


def resolved_structure_famille(
    code: str | None,
    designation: str | None = None,
    famille: str | None = None,
) -> str:
    """Type enregistré, sinon type reconnu d'après le code et la désignation."""
    stored = (famille or "").strip()
    if stored:
        return stored
    from sme_gend.services.fonction_postes_tete import structure_kind

    return structure_kind(code, designation) or ""


def structure_matches_hub_filter(
    code: str | None,
    designation: str | None,
    famille: str | None,
    *,
    famille_filter: str,
    query: str,
) -> bool:
    """Vrai si la structure passe le type et la recherche du hub."""
    from sme_gend.services.fonction_structure_policy import fold_label

    kind = resolved_structure_famille(code, designation, famille)
    wanted = (famille_filter or "").strip()
    if wanted and kind != wanted:
        return False
    needle = fold_label(query)
    if not needle:
        return True
    blob = fold_label(
        f"{code or ''} {designation or ''} {structure_famille_label(kind)}"
    )
    return needle in blob
