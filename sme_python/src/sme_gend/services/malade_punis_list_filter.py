"""Filtre recherche liste malades (inclut champs fiche satellite)."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.ui.list_table_filter import row_matches_search


def _malade_detail_blob(detail: MaladeDetail | None) -> str:
    if detail is None:
        return ""
    return " ".join(
        x
        for x in (
            detail.date_constat,
            detail.pathologie,
            detail.obs,
        )
        if x
    )


def row_matches_malade_search(
    row: ListeGeneraleRow,
    query: str,
    detail: MaladeDetail | None,
) -> bool:
    qcf = normalize_search_text(query)
    if not qcf:
        return True
    if qcf in normalize_search_text(_malade_detail_blob(detail)):
        return True
    return row_matches_search(row, query)


__all__ = ["row_matches_malade_search"]
