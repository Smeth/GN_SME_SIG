"""Tests synchronisation sexe / matricule reference."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.personnel_sexe_reference_sync import (
    normalize_matricule_key,
    normalize_person_name,
    parse_sexe_reference_xls,
)


def test_normalize_matricule_key() -> None:
    assert normalize_matricule_key("2-98-27553") == normalize_matricule_key("02-9827553")
    assert normalize_matricule_key("2000339-44G") == normalize_matricule_key("200033944G")


def test_normalize_person_name() -> None:
    assert normalize_person_name("DZERET  Lydie  Flore") == "DZERET LYDIE FLORE"
    assert normalize_person_name("OTOKA Clémence") == "OTOKA CLEMENCE"


def test_parse_feminine_sample() -> None:
    path = Path(__file__).resolve().parents[1] / "LISTE_GENERALE_SEXE_feminin.xls"
    if not path.is_file():
        return
    rows = parse_sexe_reference_xls(path, sexe="F")
    assert len(rows) > 500
    assert rows[0].sexe == "F"
    assert rows[0].matricule_key



def test_calamine_keeps_existing_xls_values_and_row_order(monkeypatch):
    import pandas as pd
    import pytest
    from sme_gend.services import personnel_sexe_reference_sync as sync
    path=Path(__file__).resolve().parents[1]/'LISTE_GENERALE_SEXE_feminin.xls'
    if not path.is_file():pytest.skip('Read-only reference XLS unavailable')
    current=sync.parse_sexe_reference_xls(path,sexe='F')
    original=pd.read_excel
    def legacy(*args,**kwargs):
        kwargs['engine']='xlrd'
        # Compare values and row order; warning wording depends on pandas.
        return original(*args,**kwargs)
    monkeypatch.setattr(pd,'read_excel',legacy)
    assert sync.parse_sexe_reference_xls(path,sexe='F')==current


def test_xlsx_parsing_preserves_missing_cells_accents_and_numeric_identifiers(tmp_path):
    from openpyxl import Workbook
    path=tmp_path/'synthetic.xlsx';book=Workbook();sheet=book.active
    for i in range(4):sheet.append(['header'])
    sheet.append([None,None,12345,None,'GND','Élise','Test'])
    sheet.append([None,None,'02-98-001',None,None,None,None])
    sheet.append([None,None,None,None,None,'Nom seul',None])
    book.save(path);book.close()
    rows=parse_sexe_reference_xls(path,sexe='F')
    assert rows[0].matricule_key==normalize_matricule_key('12345')
    assert rows[0].noms==normalize_person_name('Élise Test')
    assert all(row.sexe=='F' for row in rows)
