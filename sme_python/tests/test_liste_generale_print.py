"""Impression liste générale — modèle officiel."""

from __future__ import annotations

from datetime import datetime

from openpyxl import load_workbook

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_excel_export import export_liste_filtered
from sme_gend.services.liste_generale_print import (
    LISTE_GENERALE_OFFICIAL_TITLE,
    PRINT_COLUMNS,
    build_liste_generale_print_html,
)


def test_print_html_official_title_and_columns() -> None:
    row = ListeGeneraleRow(
        id="1",
        noms="DUPONT Jean",
        date_naissance=datetime(1985, 3, 12),
        lieu_naissance="Libreville",
        grade="LTN",
        date_grade=datetime(2020, 1, 1),
        date_service="01/07/1990",
        ndecision="REF-1",
        matricule_militaire="132211S",
        matricule_solde="3-90-18755",
        position_admin="Présent",
        fonction="Chef section",
        subdivision_arme="EMS-II",
        structure="COMGEND",
        diplome="BEM",
        date_obtention="2015",
        obs="",
        ordre_tri=None,
        presseance=None,
    )
    html = build_liste_generale_print_html([row], mois_label="2026-07", grades=[])
    assert LISTE_GENERALE_OFFICIAL_TITLE in html
    assert "SITUATION NOMINATIVE" in html
    assert "Noms et prénoms" in html
    assert "Tahoma" in html
    assert "Période SME" not in html
    mil = html.index("Matricule militaire")
    solde = html.index("Matricule solde")
    assert mil < solde
    assert html.index("132211S") < html.index("3-90-18755")
    assert "DUPONT Jean" in html
    assert "132211S" in html
    assert "Date de nomination" in html
    assert "decision" in html or "décision" in html or "&#" in html
    assert len(PRINT_COLUMNS) == 16
    subset = build_liste_generale_print_html(
        [row],
        mois_label="2026-07",
        columns=["noms", "grade", "structure"],
    )
    assert "Noms et prénoms" in subset
    assert "Grade" in subset
    assert "Matricule solde" not in subset
    assert "DUPONT Jean" in subset


def test_print_html_title_once_on_multipage() -> None:
    def _row(i: int) -> ListeGeneraleRow:
        return ListeGeneraleRow(
            id=str(i),
            noms=f"AGENT {i}",
            date_naissance=None,
            lieu_naissance=None,
            grade="Sdt",
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin="Présent",
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure="U001",
            obs=None,
            ordre_tri=None,
            presseance=None,
        )

    html = build_liste_generale_print_html([_row(i) for i in range(25)])
    assert html.count(LISTE_GENERALE_OFFICIAL_TITLE) == 1
    assert html.count("<table>") == 1
    assert "<thead>" not in html
    assert "hdr-row" in html
    assert html.count("AGENT 24") == 1


def test_liste_generale_exports_grade_code_for_accented_label(tmp_path) -> None:
    row = ListeGeneraleRow(
        id="1",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="Général de brigade",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        structure="COMGEND",
        diplome=None,
        date_obtention=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    grades = [
        GradeRefRow(
            code="GDB",
            libelle="GENERAL DE BRIGADE",
            pref_ord=1,
            effectif_g=None,
        )
    ]

    pdf_html = build_liste_generale_print_html([row], grades=grades)
    excel_path = tmp_path / "liste_generale.xlsx"
    export_liste_filtered([row], excel_path, grades=grades)
    workbook = load_workbook(excel_path, read_only=True)
    worksheet = workbook.active
    grade_column = next(
        cell.column for cell in worksheet[2] if cell.value == "Grade"
    )

    assert "<td class=''>GDB</td>" in pdf_html
    assert worksheet.cell(3, grade_column).value == "GDB"
    assert "Général de brigade" not in pdf_html
    workbook.close()


# These are document-layout units; native journal persistence is tested separately.
import pytest

@pytest.fixture(autouse=True)
def isolated_document_journal(monkeypatch,tmp_path):
    from sme_gend.config.settings import Settings
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:Settings(database_path=tmp_path/'unused.mdb',mysql_view_mois='2026-09'))
    monkeypatch.setattr('sme_gend.services.doc_gen_log._append_doc_gen_mysql',lambda **kw:True)
