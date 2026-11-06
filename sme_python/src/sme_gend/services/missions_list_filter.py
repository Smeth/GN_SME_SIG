"""Filtre recherche liste missions."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MissionDetail
from sme_gend.ui.list_table_filter import row_matches_search


def mission_detail_for_row(
    row: ListeGeneraleRow,
    details: dict[str, MissionDetail],
) -> MissionDetail | None:
    key = row.satellite_join_key()
    if key and key in details:
        return details[key]
    if row.id and row.id in details:
        return details[row.id]
    return None


def _mission_blob(detail: MissionDetail | None) -> str:
    if detail is None:
        return ""
    return " ".join(
        x
        for x in (
            detail.nature,
            detail.lieu_mission,
            detail.refnds,
            detail.date_depart,
        )
        if x
    )


def row_matches_mission_search(
    row: ListeGeneraleRow,
    query: str,
    detail: MissionDetail | None,
) -> bool:
    qcf = normalize_search_text(query)
    if not qcf:
        return True
    if qcf in normalize_search_text(_mission_blob(detail)):
        return True
    return row_matches_search(row, query)


__all__ = ["mission_detail_for_row", "row_matches_mission_search"]
