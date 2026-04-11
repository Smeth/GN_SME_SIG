"""Règles de correspondance position administrative — situation numérique (alignement Excel)."""

from __future__ import annotations

from collections.abc import Callable

from sme_gend.models.liste_generale import ListeGeneraleRow


def _norm(s: str | None) -> str:
    return (s or "").strip()


def is_present_strict(row: ListeGeneraleRow) -> bool:
    """Comme la liste des présents : sous-chaîne « présent » (accents neutralisés). Stage interne exclu."""
    p = _norm(row.position_admin).lower().replace("é", "e").replace("è", "e")
    return "present" in p


def _eq_padmin(row: ListeGeneraleRow, code: str) -> bool:
    return _norm(row.position_admin) == code


def _one_of_padmin(row: ListeGeneraleRow, codes: tuple[str, ...]) -> bool:
    p = _norm(row.position_admin)
    return p in codes


# Codes Padmin alignés sur les formules du gabarit numerique.xlsx (feuille NUMERIQUE / INDISPO).
PADMIN_DETACHE = "Détaché"
PADMIN_DESERTEUR = "Déserteur"
PADMIN_CONGE_ADMIN = "CongéAdmin"
PADMIN_CONGE_EXP = "CongéExp"
PADMIN_MISSION = "Mission"
PADMIN_MALADE = "Malade"
PADMIN_INST_MR = "Inst Mise Route"
PADMIN_STAGE_LOCAL = "StageLocal"
PADMIN_STAGE_EX = "StageEx"
PADMIN_PUNIS = "Punis"
PADMIN_PRISON_MA = "Prison MA"


def predicate_inscrit(_row: ListeGeneraleRow) -> bool:
    return True


def row_predicate_for_rubrique(key: str) -> Callable[[ListeGeneraleRow], bool]:
    """Clé interne (stable) → prédicat sur ListeGeneraleRow."""
    k = key.strip().lower()
    if k == "inscrits":
        return predicate_inscrit
    if k == "presents":
        return is_present_strict
    if k == "detache":
        return lambda r: _eq_padmin(r, PADMIN_DETACHE)
    if k == "deserteur":
        return lambda r: _eq_padmin(r, PADMIN_DESERTEUR)
    if k == "conge_admin":
        return lambda r: _eq_padmin(r, PADMIN_CONGE_ADMIN)
    if k == "conge_exp":
        return lambda r: _eq_padmin(r, PADMIN_CONGE_EXP)
    if k == "mission":
        return lambda r: _eq_padmin(r, PADMIN_MISSION)
    if k == "malade":
        return lambda r: _eq_padmin(r, PADMIN_MALADE)
    if k == "inst_mr":
        return lambda r: _eq_padmin(r, PADMIN_INST_MR)
    if k == "stage_local":
        return lambda r: _eq_padmin(r, PADMIN_STAGE_LOCAL)
    if k == "stage_ex":
        return lambda r: _eq_padmin(r, PADMIN_STAGE_EX)
    if k == "prison_ma":
        return lambda r: _one_of_padmin(r, (PADMIN_PUNIS, PADMIN_PRISON_MA))
    raise ValueError(f"Rubrique inconnue : {key!r}")
