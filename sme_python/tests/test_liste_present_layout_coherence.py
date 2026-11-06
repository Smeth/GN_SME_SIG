"""Tests cohérence layout liste des présents ↔ base."""

from __future__ import annotations

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.models.structure import StructureRow
from sme_gend.services.liste_present_layout_coherence import (
    filter_layout_to_database,
    merge_subdivisions_for_structure,
    merged_rg_subdivisions,
    reconcile_layout_with_database,
)


def test_filter_layout_removes_unknown_structure_codes():
    cfg = ListeLayoutConfig(
        sections=[
            SectionBlock(id="a", order=0, structure_code="COMGEND"),
            SectionBlock(id="b", order=1, structure_code="FAKE-XX"),
        ]
    )
    db = [StructureRow(code="COMGEND", designation="Commandement", ordre_tri=1)]
    out, removed = filter_layout_to_database(cfg, db)
    assert removed == ["FAKE-XX"]
    assert [s.structure_code for s in out.sections] == ["COMGEND"]


def test_merge_subdivisions_rg_keeps_template_and_appends_mdb():
    template = ["COMMANDEMENT RG-BZV", "ONU"]
    mdb = ["COMMANDEMENT", "DETACHEMENT ONU", "ETAT-MAJOR"]
    merged = merged_rg_subdivisions("RG-BZV", template, mdb)
    assert merged[0] == "COMMANDEMENT RG-BZV"
    assert "ETAT-MAJOR" in merged
    assert merged.index("ONU") < merged.index("ETAT-MAJOR")


def test_merge_subdivisions_normal_preserves_order_and_appends_db():
    template = ["Direction", "SECRETARIAT"]
    db = ["SECRETARIAT", "DIVISION GP"]
    merged = merge_subdivisions_for_structure("DPF", template, db)
    assert merged[:2] == ["Direction", "SECRETARIAT"]
    assert "DIVISION GP" in merged


def test_reconcile_adds_missing_structures_from_db():
    cfg = ListeLayoutConfig(
        sections=[SectionBlock(id="a", order=0, structure_code="COMGEND")]
    )
    db = [
        StructureRow(code="COMGEND", designation="COM", ordre_tri=1),
        StructureRow(code="DPF", designation="DPF", ordre_tri=2),
    ]

    def _subs(code: str) -> tuple[list[str], list[int]]:
        if code == "DPF":
            return ["SECRETARIAT"], [1]
        return [], []

    out, report = reconcile_layout_with_database(
        cfg, db, db_subdivisions_for=_subs, append_missing_structures=True
    )
    codes = [s.structure_code for s in out.sections]
    assert "DPF" in codes
    assert "DPF" in report.added_structure_codes
