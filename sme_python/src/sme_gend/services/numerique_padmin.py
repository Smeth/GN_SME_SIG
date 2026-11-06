"""Règles de correspondance position administrative — situation numérique (alignement Excel)."""

from __future__ import annotations

from collections.abc import Callable

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.padmin_present import padmin_counts_as_present, default_presence_index
from sme_gend.services.position_codes import position_code


def _norm(s: str | None) -> str:
    return (s or "").strip()


def is_present_strict(row: ListeGeneraleRow, *, index=None) -> bool:
    """Prédicat rubrique « présents » (matrice NUMERIQUE, blocs dérivés)."""
    return padmin_counts_as_present(row.position_admin, "present",
                                   index=index if index is not None else default_presence_index())


def _eq_padmin(row: ListeGeneraleRow, code: str) -> bool:
    actual, expected = position_code(row.position_admin), position_code(code)
    if actual is not None and expected is not None:
        return actual == expected
    return _norm(row.position_admin).casefold() == code.casefold()


def _one_of_padmin(row: ListeGeneraleRow, codes: tuple[str, ...]) -> bool:
    return any(_eq_padmin(row, code) for code in codes)


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
