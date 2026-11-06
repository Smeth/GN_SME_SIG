"""Tests export Excel module Stages."""

from __future__ import annotations

from pathlib import Path

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.services.stages_export import export_stages_workbook
from openpyxl import load_workbook


def _row(**kw) -> ListeGeneraleRow:
    base = dict(
        id="1",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="Colonel",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="StageEx",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    base.update(kw)
    return ListeGeneraleRow(**base)


def test_export_stages_workbook_writes_file(tmp_path: Path) -> None:
    dest = tmp_path / "stages.xlsx"
    export_stages_workbook(
        dest,
        title="Stages extérieurs",
        rows=[_row()],
        details={"1": StageDetail("NATO", "Paris", "2026-01-01 — 2026-06-30")},
    )
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert ws["A1"].value == "Stages extérieurs"
        assert ws["A1"].font.bold is True
        assert ws["A2"].value == "Id liaison"
        assert ws["B3"].value == "DUPONT Jean"
    finally:
        wb.close()


# These are document-layout units; native journal persistence is tested separately.
import pytest

@pytest.fixture(autouse=True)
def isolated_document_journal(monkeypatch,tmp_path):
    from sme_gend.config.settings import Settings
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'))
    monkeypatch.setattr('sme_gend.services.doc_gen_log._append_doc_gen_mysql',lambda **kw:True)
