"""Filtre recherche module Congés."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.list_table_filter import row_matches_search


def row_matches_conge_search(
    row: ListeGeneraleRow,
    query: str,
    conge: AgentCongeRow | None,
) -> bool:
    qcf = normalize_search_text(query)
    if not qcf:
        return True
    if conge is not None:
        blob = " ".join(
            x
            for x in (
                conge.type_label,
                conge.date_debut,
                conge.date_fin,
                conge.quota_jours,
                conge.observation,
            )
            if x
        ).casefold()
        if qcf in normalize_search_text(blob):
            return True
    return row_matches_search(row, query)
