"""Filtre recherche liste désertions."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import DeserteurDetail
from sme_gend.ui.list_table_filter import row_matches_search


def desertion_detail_for_row(
    row: ListeGeneraleRow,
    details: dict[str, DeserteurDetail],
) -> DeserteurDetail | None:
    key = row.satellite_join_key()
    if key and key in details:
        return details[key]
    if row.id and row.id in details:
        return details[row.id]
    return None


def row_matches_desertion_search(
    row: ListeGeneraleRow,
    query: str,
    detail: DeserteurDetail | None,
) -> bool:
    qcf = normalize_search_text(query)
    if not qcf:
        return True
    if detail and detail.dat_desertion and qcf in normalize_search_text(detail.dat_desertion):
        return True
    return row_matches_search(row, query)


__all__ = ["desertion_detail_for_row", "row_matches_desertion_search"]
