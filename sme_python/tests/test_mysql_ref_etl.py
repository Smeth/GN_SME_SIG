"""Tests ETL ref_* (sans MySQL)."""

from __future__ import annotations

from datetime import date

import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
from sme_gend.services.mysql_ref_etl import (
    RefEtlCache,
    _resolve_leaf_node_id,
    cdsme_to_annee_mois,
)


def test_first_and_last_day_of_month():
    assert first_day_of_month("2026-04") == date(2026, 4, 1)
    assert last_day_of_month("2026-04") == date(2026, 4, 30)


def test_cdsme_to_annee_mois():
    assert cdsme_to_annee_mois("042026") == "2026-04"
    assert cdsme_to_annee_mois("2026-04") == "2026-04"


def test_resolve_leaf_by_design_t():
    cache = RefEtlCache()
    cache.leaf_node_by_design_t["esr pointe-noire"] = 42
    assert _resolve_leaf_node_id(cache, "ESR POINTE-NOIRE", None) == 42


def test_resolve_leaf_by_flat_code_t():
    cache = RefEtlCache()
    flat = [
        SubdivisionFlatRow(
            code_u="U1",
            design_u="Unité",
            code_d="D1",
            design_d="Div",
            code_t="T1",
            design_t="Feuille",
        )
    ]
    cache.leaf_node_by_flat_key["u1|d1|t1"] = 7
    assert _resolve_leaf_node_id(cache, "T1", flat) == 7


def test_cdsme_invalid_returns_none_or_canonical():
    assert cdsme_to_annee_mois("") is None
    assert cdsme_to_annee_mois("invalid") is None
