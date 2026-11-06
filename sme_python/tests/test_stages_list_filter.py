"""Tests filtre nature module Stages."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.services.stages_list_filter import (
    NATURE_FILTER_EMPTY,
    build_nature_filter_options,
    row_matches_nature_filter,
    row_matches_stage_search,
)


def _row(rid: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=rid,
        noms="X",
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="StageLocal",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_build_nature_filter_options_orders_by_frequency() -> None:
    details = {
        "1": StageDetail("ALPHA", None, None),
        "2": StageDetail("ALPHA", None, None),
        "3": StageDetail("BETA", None, None),
        "4": StageDetail(None, None, None),
    }
    options = build_nature_filter_options(
        [_row("1"), _row("2"), _row("3"), _row("4")],
        details,
        top_limit=5,
    )
    labels = [label for label, _ in options]
    assert labels[0] == "Toutes les natures"
    assert "ALPHA (2)" in labels
    assert "BETA (1)" in labels
    assert any("(Sans nature" in label for label in labels)


def test_row_matches_stage_search_on_nature() -> None:
    row = _row("1")
    detail = StageDetail("Formatage UPC-12", "CI KINTELE", "12/01/2026")
    assert row_matches_stage_search(row, "upc-12", detail)
    assert not row_matches_stage_search(row, "inconnu", detail)


def test_row_matches_nature_filter() -> None:
    d = StageDetail("CYNO", None, None)
    assert row_matches_nature_filter(d, "")
    assert row_matches_nature_filter(d, "CYNO")
    assert not row_matches_nature_filter(d, "AUTRE")
    assert row_matches_nature_filter(None, NATURE_FILTER_EMPTY)
    assert not row_matches_nature_filter(d, NATURE_FILTER_EMPTY)
