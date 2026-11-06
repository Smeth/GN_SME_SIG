"""Export référentiel structures."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from sme_gend.config.structure_famille import structure_famille_label
from sme_gend.models.structure import StructureRow
from sme_gend.services.structure_referentiel_export import export_structures_referentiel


def test_export_structures_includes_famille(tmp_path: Path) -> None:
    dest = tmp_path / "ref.xlsx"
    rows = [
        StructureRow(
            code="DSF",
            designation="Direction des…",
            ordre_tri=1,
            famille_unite="DIRECTION",
        )
    ]
    export_structures_referentiel(
        dest,
        rows,
        subdiv_counts={"DSF": 3},
        fonction_counts={"DSF": 5},
        effectif_counts={"DSF": 120},
        mois_label="2026-07",
    )
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert "2026-07" in str(ws["A1"].value)
        assert ws["A4"].value == "DSF"
        assert ws["C4"].value == structure_famille_label("DIRECTION")
        assert ws["E4"].value == 3
        assert ws["G4"].value == 120
    finally:
        wb.close()


# These are document-layout units; native journal persistence is tested separately.
import pytest

@pytest.fixture(autouse=True)
def isolated_document_journal(monkeypatch,tmp_path):
    from sme_gend.config.settings import Settings
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'))
    monkeypatch.setattr('sme_gend.services.doc_gen_log._append_doc_gen_mysql',lambda **kw:True)
