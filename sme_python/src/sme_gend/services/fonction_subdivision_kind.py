"""Classification du type de subdivision d'arme (division, section, …)."""

from __future__ import annotations

from sme_gend.services.fonction_structure_policy import fold_label


def _norm(value: str | None) -> str:
    return " ".join(fold_label(value).replace("'", " ").replace("’", " ").split())


def _is_srj(tokens: set[str]) -> bool:
    if "SRJ" in tokens:
        return True
    if "SECTION" not in tokens:
        return False
    recherche = "RECHERCHE" in tokens or "RECHERCHES" in tokens
    judiciaire = any(token.startswith("JUDICIAIR") for token in tokens)
    return recherche and judiciaire


def subdivision_kind(libelle: str | None) -> str | None:
    """Type d'unité de la subdivision, ou None."""
    tokens = set(_norm(libelle).split())
    if _is_srj(tokens):
        return "SRJ"
    if "DIVISION" in tokens:
        return "DIVISION"
    if "DEPARTEMENT" in tokens:
        return "DEPARTEMENT"
    if "SECTION" in tokens:
        return "SECTION"
    if "SECRETARIAT" in tokens:
        return "SECRETARIAT"
    if "CABINET" in tokens:
        return "CABINET"
    if "POLE" in tokens or "POLES" in tokens:
        return "POLE"
    if "SERVICE" in tokens or "SERVICES" in tokens:
        return "SERVICE"
    if "ESCADRON" in tokens or "ESSIG" in tokens:
        return "ESCADRON"
    if "PSIG" in tokens or "PELOTON" in tokens:
        return "PELOTON"
    if "BRIGADE" in tokens or "BBRIGADE" in tokens:
        return "BRIGADE"
    if "COMPAGNIE" in tokens:
        return "COMPAGNIE"
    return None
