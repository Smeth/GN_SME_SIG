"""Périmètre SME DPF — Direction du Personnel et de la Formation (source MySQL)."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.numerique_padmin import (
    PADMIN_DESERTEUR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
    is_present_strict,
    row_predicate_for_rubrique,
)
DPF_STRUCTURE_CODES = frozenset({"DPF", "CS-DPF"})

# Filtre « Vue » (module SME DPF) — clés stockées dans le combo.
DPF_VIEW_ALL = ""
DPF_VIEW_DPF = "dpf"
DPF_VIEW_CONTROLE = "controle"
DPF_VIEW_COMGEND = "comgend"

DPF_VIEW_FILTER_ITEMS: tuple[tuple[str, str], ...] = (
    ("Tous", DPF_VIEW_ALL),
    ("DPF", DPF_VIEW_DPF),
    ("Contrôle spécial DPF", DPF_VIEW_CONTROLE),
    ("COMGEND lié DPF", DPF_VIEW_COMGEND),
)


def row_matches_dpf_view_filter(row: ListeGeneraleRow, view_key: str) -> bool:
    """Filtre d'affichage SME DPF (combo « Vue »)."""
    key = (view_key or "").strip()
    if not key or key == DPF_VIEW_ALL:
        return True
    code = _structure_code(row)
    if key == DPF_VIEW_DPF:
        return code == "DPF"
    if key == DPF_VIEW_CONTROLE:
        return code == "CS-DPF"
    if key == DPF_VIEW_COMGEND:
        if code != "COMGEND":
            return False
        blob = f"{row.fonction or ''} {row.subdivision_arme or ''}".upper()
        return "DPF" in blob
    return True


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _structure_code(row: ListeGeneraleRow) -> str:
    code = _norm(row.structure).upper()
    return {"CS-PDF": "CS-DPF", "CS/DPF": "CS-DPF"}.get(code, code)


def is_dpf_control_row(row: ListeGeneraleRow) -> bool:
    return _structure_code(row) == "CS-DPF"


def row_in_sme_dpf_scope(row: ListeGeneraleRow) -> bool:
    """
    Personnel rattaché à la SME DPF (aligné gabarit ``SME_DPF_*.xlsx``).

    Structures ``DPF`` et ``CS-DPF`` ; ``COMGEND`` si la fonction mentionne la DPF.
    """
    code = _structure_code(row)
    if code in DPF_STRUCTURE_CODES:
        return True
    if code == "COMGEND":
        blob = f"{row.fonction or ''} {row.subdivision_arme or ''}".upper()
        return "DPF" in blob
    return False


def filter_dpf_rows(rows: list[ListeGeneraleRow]) -> list[ListeGeneraleRow]:
    return [r for r in rows if row_in_sme_dpf_scope(r)]


def is_dpf_present(row: ListeGeneraleRow) -> bool:
    return is_present_strict(row)


def is_dpf_controle_attente(row: ListeGeneraleRow) -> bool:
    """Section « en attente d'affectation » — CS-DPF, présents."""
    return _structure_code(row) == "CS-DPF" and is_present_strict(row)


def is_dpf_controle_malade(row: ListeGeneraleRow) -> bool:
    return _structure_code(row) == "CS-DPF" and _norm(row.position_admin).casefold() == PADMIN_MALADE.casefold()


def is_dpf_stagiaire(row: ListeGeneraleRow) -> bool:
    p = _norm(row.position_admin).casefold()
    return p in {
        PADMIN_STAGE_LOCAL.casefold(),
        PADMIN_STAGE_EX.casefold(),
        "stageinterne",
        "stage interne",
    }


def is_dpf_indisponible(row: ListeGeneraleRow) -> bool:
    if is_present_strict(row):
        return False
    p = _norm(row.position_admin).casefold()
    return bool(p) and p not in {"", "present", "présent"}


def indisponible_bucket(row: ListeGeneraleRow) -> str:
    p = _norm(row.position_admin).casefold()
    if p == PADMIN_MALADE.casefold():
        return "malades"
    if p == PADMIN_DESERTEUR.casefold():
        return "deserteurs"
    if p == PADMIN_MISSION.casefold():
        return "missions"
    if p in {"stageinterne", "stage interne"}:
        return "stages_internes"
    if p == PADMIN_STAGE_EX.casefold():
        return "stages_etranger"
    if p == PADMIN_STAGE_LOCAL.casefold():
        return "stages_locaux"
    if row_predicate_for_rubrique("conge_admin")(row) or row_predicate_for_rubrique("conge_exp")(row):
        return "conges"
    return "autres"
