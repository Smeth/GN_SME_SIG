"""Tests export Excel — styles sur cellules fusionnées (writable_cell)."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.services.excel_template_exporter import export_simple_fallback
from sme_gend.services.gain_perte_export import export_gain_perte_workbook
from sme_gend.services.liste_present_rubric import SANS_SUBDIVISION_KEY
from sme_gend.services.liste_generale_print import LISTE_GENERALE_OFFICIAL_TITLE
from sme_gend.services.malade_punis_export import export_malades_workbook, export_punis_workbook
from sme_gend.services.personnel_excel_export import export_liste_filtered


def _liste_row(**kw) -> ListeGeneraleRow:
    base = dict(
        id="42",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="Colonel",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Malade",
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


def test_export_liste_generale_official_excel(tmp_path: Path) -> None:
    dest = tmp_path / "lg.xlsx"
    row = _liste_row(
        matricule_militaire="132211S",
        matricule_solde="3-90-18755",
    )
    export_liste_filtered([row], dest, mois_label="2026-07")
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert ws["A1"].value == LISTE_GENERALE_OFFICIAL_TITLE
        assert ws["A1"].font.bold is True
        # Excel officiel : titre (1) + mois (2) + en-têtes (3) + données (4+).
        assert "2026-07" in str(ws["A2"].value)
        assert ws["A3"].value == "N°"
        assert ws["B3"].value == "Noms et prénoms"
        assert ws["B4"].value == "DUPONT Jean"
        assert ws["H4"].value == "132211S"
        assert ws["I4"].value == "3-90-18755"
        assert ws["J4"].value == "Malade"
        assert ws.print_title_rows in (None, "")
        assert ws.page_setup.orientation == "landscape"
    finally:
        wb.close()


def test_export_malades_workbook_title_font(tmp_path: Path) -> None:
    dest = tmp_path / "malades.xlsx"
    row = _liste_row()
    export_malades_workbook(
        dest,
        rows=[row],
        details={row.satellite_join_key(): MaladeDetail("2026-01-15", "Grippe", "Repos")},
    )
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert ws["A1"].value == "Malades"
        assert ws["A1"].font.bold is True
        assert ws["A2"].font.bold is True
        assert ws["B3"].value == "DUPONT Jean"
    finally:
        wb.close()


def test_export_punis_workbook_title_font(tmp_path: Path) -> None:
    dest = tmp_path / "punis.xlsx"
    export_punis_workbook(dest, rows=[_liste_row(position_admin="Puni")])
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert ws["A1"].value == "Punis & détention"
        assert ws["A1"].font.bold is True
        assert ws["A2"].font.bold is True
    finally:
        wb.close()


def test_export_gain_perte_workbook_title_font(tmp_path: Path) -> None:
    dest = tmp_path / "gp.xlsx"
    export_gain_perte_workbook(
        dest,
        rows=[
            GainPerteRow(
                id="1",
                grade="Colonel",
                nom="DUPONT",
                objet="GAIN",
                cdsme="062026",
                observ="AJOUT",
                mois_ym="2026-06",
            )
        ],
        mois_ym="2026-06",
    )
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert "Gains & Pertes" in str(ws["A1"].value)
        assert ws["A1"].font.bold is True
        assert ws["A2"].font.bold is True
    finally:
        wb.close()


def test_export_simple_fallback_merged_styles(tmp_path: Path) -> None:
    dest = tmp_path / "liste_present.xlsx"
    row = _liste_row()
    sections = [
        (
            "DIRECTION",
            "CS-DPF",
            "U001",
            [(SANS_SUBDIVISION_KEY, [row])],
        )
    ]
    export_simple_fallback(dest, sections)
    wb = load_workbook(dest)
    try:
        ws = wb.active
        assert ws["A1"].font.bold is True
        assert ws["A3"].font.bold is True
        assert ws["A3"].fill.fgColor.rgb is not None
    finally:
        wb.close()


# These are document-layout units; native journal persistence is tested separately.
import pytest

@pytest.fixture(autouse=True)
def isolated_document_journal(monkeypatch,tmp_path):
    from sme_gend.config.settings import Settings
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'))
    monkeypatch.setattr('sme_gend.services.doc_gen_log._append_doc_gen_mysql',lambda **kw:True)
