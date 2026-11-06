"""Typologie structures."""

from __future__ import annotations

from sme_gend.config.structure_famille import (
    normalize_structure_famille,
    structure_famille_label,
)
from sme_gend.models.structure import StructureRow


def test_structure_famille_label() -> None:
    assert structure_famille_label("DIRECTION") == "Direction"
    assert structure_famille_label(None) == "—"
    assert normalize_structure_famille("ECOLE") == "ECOLE"
    assert normalize_structure_famille("invalid") is None


def test_structure_row_famille_from_sequence() -> None:
    row = StructureRow.from_sequence(("U001", "Unité test", 1, "GROUPEMENT"))
    assert row.famille_unite == "GROUPEMENT"
