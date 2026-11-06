"""Tests affichage grade / subdivision / structure."""

from __future__ import annotations

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.structure_label_format import (
    format_structure_code_display,
    format_structure_display,
    resolve_structure_code,
    structure_combo_items,
)
from sme_gend.services.subdivision_label_format import format_subdivision_arme_display

_STRUCTS = [
    StructureRow(code="COMGEND", designation="COMMANDEMENT DE LA GENDARMERIE", ordre_tri=1),
    StructureRow(code="IT", designation="INSPECTION TECHNIQUE", ordre_tri=2),
]


def test_grade_combo_items_resolves_libelle_extra() -> None:
    from sme_gend.services.grade_label_format import grade_combo_items

    grades = [GradeRefRow(code="CAP", libelle="Capitaine", pref_ord=1, effectif_g=None)]
    items = grade_combo_items(grades, extra_raw=["Capitaine", "MDL"])
    by_code = {code: label for label, code in items}
    assert by_code["CAP"] == "CAP — Capitaine"
    assert by_code["MDL"] == "MDL"


def test_format_grade_display_resolves_libelle_to_code() -> None:
    grades = [GradeRefRow(code="CAP", libelle="Capitaine", pref_ord=1, effectif_g=None)]
    assert format_grade_display("Capitaine", grades) == "CAP"
    assert format_grade_display("CAP", grades) == "CAP"


def test_format_grade_display_normalizes_hyphens() -> None:
    grades = [
        GradeRefRow(code="ADC", libelle="ADJUDANT-CHEF", pref_ord=1, effectif_g=None),
    ]
    assert format_grade_display("ADJUDANT CHEF", grades) == "ADC"
    assert format_grade_display("adjudant-chef", grades) == "ADC"


def test_grade_combo_items_dedupes_libelle_and_code() -> None:
    from sme_gend.services.grade_label_format import grade_combo_items

    grades = [
        GradeRefRow(code="ADC", libelle="ADJUDANT-CHEF", pref_ord=1, effectif_g=None),
        GradeRefRow(code="ADJUDANT-CHEF", libelle="ADJUDANT-CHEF", pref_ord=2, effectif_g=None),
    ]
    items = grade_combo_items(grades, extra_raw=["ADJUDANT CHEF", "ADC"])
    codes = [code for _, code in items]
    assert codes == ["ADC"]
    assert items[0][0] == "ADC — ADJUDANT-CHEF"


def test_format_structure_display_by_code() -> None:
    assert format_structure_display("COMGEND", _STRUCTS) == (
        "COMGEND — COMMANDEMENT DE LA GENDARMERIE"
    )


def test_format_structure_code_display_by_code() -> None:
    assert format_structure_code_display("COMGEND", _STRUCTS) == "COMGEND"
    assert (
        format_structure_code_display("COMMANDEMENT DE LA GENDARMERIE", _STRUCTS)
        == "COMGEND"
    )


def test_resolve_structure_code_from_designation() -> None:
    assert resolve_structure_code("COMMANDEMENT DE LA GENDARMERIE", _STRUCTS) == "COMGEND"


def test_structure_combo_items_sorted() -> None:
    items = structure_combo_items(_STRUCTS)
    assert items[0][1] == "COMGEND"
    assert "COMGEND —" in items[0][0]


def test_format_subdivision_name_only() -> None:
    flat = [
        SubdivisionFlatRow(
            code_u="ESR",
            design_u="Escadron",
            code_d="",
            design_d="",
            code_t="NGO",
            design_t="ESCADRON DE SECURITE ROUTIERE NGO",
        )
    ]
    assert format_subdivision_arme_display("NGO", flat, name_only=True) == (
        "ESCADRON DE SECURITE ROUTIERE NGO"
    )
    assert format_subdivision_arme_display("ESR NGO", flat, name_only=True) == "NGO"
