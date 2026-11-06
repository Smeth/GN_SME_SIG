"""Position administrative → fiche satellite (malade, mission, stage)."""

from __future__ import annotations

from typing import Literal

from sme_gend.services.numerique_padmin import (
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)

SatelliteKind = Literal[
    "malade", "mission", "stage_local", "stage_ex", "stage_interne"
]

_STAGE_INTERNE = "stage interne"


def satellite_kind(position_admin: str | None) -> SatelliteKind | None:
    from sme_gend.services.position_codes import position_code
    code = position_code(position_admin)
    if code in ('malade', 'mission', 'stage_local', 'stage_ex', 'stage_interne'):
        return code
    p = " ".join((position_admin or "").casefold().split())
    if not p:
        return None
    if p == PADMIN_MALADE.casefold():
        return "malade"
    if p == PADMIN_MISSION.casefold():
        return "mission"
    if p.replace(" ", "") == "stageinterne":
        return "stage_interne"
    if p == PADMIN_STAGE_LOCAL.casefold():
        return "stage_local"
    if p == PADMIN_STAGE_EX.casefold():
        return "stage_ex"
    return None


def padmin_label_for_kind(kind: SatelliteKind) -> str:
    if kind == "malade":
        return PADMIN_MALADE
    if kind == "mission":
        return PADMIN_MISSION
    if kind == "stage_local":
        return PADMIN_STAGE_LOCAL
    if kind == "stage_interne":
        return "Stage interne"
    return PADMIN_STAGE_EX


def kind_requires_satellite_prompt(
    old_padmin: str | None,
    new_padmin: str | None,
) -> SatelliteKind | None:
    """Retourne le type de fiche à proposer si la nouvelle position le nécessite."""
    new_k = satellite_kind(new_padmin)
    if new_k is None:
        return None
    if satellite_kind(old_padmin) == new_k:
        return None
    return new_k
