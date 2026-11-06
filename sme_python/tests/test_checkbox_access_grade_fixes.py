"""Mouse/keyboard checkbox regression and abbreviated Access personnel grades."""
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QAbstractItemView
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteAgeService, LimiteDateResult
from sme_gend.services import mysql_bundle_export_to_access as access


def person(grade="CAPITAINE"):
    return ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)} | dict(
        id="SYNTHETIC", noms="PERSONNE FICTIVE", grade=grade, access_join_id="42")))


@pytest.mark.parametrize("kind",["attestations","soit_transmis"])
def test_mouse_and_keyboard_toggle_without_opening_a_text_editor(monkeypatch,kind):
    from sme_gend.ui.views import limite_age_view as la, soit_transmis_view as st
    app=QApplication.instance() or QApplication([])
    settings=Settings(database_path=Path("synthetic-unused"))
    monkeypatch.setattr(la,"can_export",lambda:False)
    monkeypatch.setattr(st,"can_export",lambda:False)
    if kind=="attestations":
        view=la._AttestationsTab(settings,LimiteAgeService())
        view._all_rows=[(person(),LimiteDateResult(date(2040,1,1),"âge",None,None,None))]
        view._fresh=True;view._apply_filter()
    else:
        monkeypatch.setattr(st.SoitTransmisService,"load",lambda *a:SoitTransmisConfig())
        view=st.SoitTransmisView(settings)
        view._needs_reload=False
        view._model.load([person()])
    view.resize(1400,700);view.show();app.processEvents()
    table=view._table;model=view._model;index=model.index(0,0)
    try:
        assert not (model.flags(index) & Qt.ItemFlag.ItemIsEditable)
        assert model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable
        assert table.editTriggers()==QAbstractItemView.EditTrigger.NoEditTriggers
        rect=table.visualRect(index)
        QTest.mouseClick(table.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(rect.left()+8,rect.center().y()))
        app.processEvents()
        assert model.checked_count()==1
        table.setCurrentIndex(index);table.setFocus()
        QTest.keyClick(table,Qt.Key.Key_Space)
        assert model.checked_count()==0
        QTest.mouseDClick(table.viewport(),Qt.MouseButton.LeftButton,pos=table.visualRect(model.index(0,1)).center())
        QTest.keyClicks(table,"abc")
        assert table.viewport().findChildren(QLineEdit)==[]
        assert model.data(model.index(0,1))=="CAPITAINE"
    finally:
        view.close();view.deleteLater();app.processEvents()


@pytest.fixture
def codes():
    return access._AccessGradeCodes([("CNE","CAPITAINE"),("COL","COLONEL"),("MAJ","MAJOR"),
                                     ("COL-MAJ","COLONEL-MAJOR")])


@pytest.mark.parametrize("raw,expected",[("CAPITAINE","CNE"),(" capitaine ","CNE"),
    ("cne","CNE"),("COLONEL-MAJOR","COL-MAJ"),("Colonel Major","COL-MAJ"),
    ("Major","MAJ"),(None,None),(" ",None)])
def test_access_grade_codes_resolve_exact_labels_and_codes(codes,raw,expected):
    assert codes.resolve(raw)==expected


def test_unknown_or_ambiguous_grade_never_silently_exports_a_full_name(codes):
    with pytest.raises(ValueError,match="absent du référentiel"):
        codes.resolve("CAPITAINE INCONNU")
    ambiguous=access._AccessGradeCodes([("CNE","CAPITAINE"),("CAP","CAPITAINE")])
    with pytest.raises(ValueError,match="ambigu"):
        ambiguous.resolve("CAPITAINE")
    assert ambiguous.resolve("CNE")=="CNE"


def test_access_liste_generale_writes_short_code_without_mutating_source(codes):
    cursor=MagicMock();original=person()
    rows=[access.ExportListeRow(1,original,"42")]
    access._write_liste_generale(cursor,rows,access_repo=MagicMock(),grade_codes=codes)
    sql,params=cursor.execute.call_args.args
    assert "INSERT INTO ListeGenerale" in sql and params[4]=="CNE"
    assert original.grade=="CAPITAINE"


def test_invalid_grade_aborts_whole_personnel_list_before_first_write(codes):
    cursor=MagicMock()
    rows=[access.ExportListeRow(1,person(),"42"),access.ExportListeRow(2,person("INCONNU"),"43")]
    with pytest.raises(ValueError):
        access._write_liste_generale(cursor,rows,access_repo=MagicMock(),grade_codes=codes)
    cursor.execute.assert_not_called()


def test_gain_perte_archive_writes_abbreviated_snapshot_grade(codes):
    cursor=MagicMock();mysql=MagicMock();repo=MagicMock()
    mysql.fetchall.return_value=[("42","CAPITAINE","FICTIF","GAIN","TYPE","092026","MOTIF",None,1)]
    count=access._write_gain_perte(cursor,mysql,gp_repo=repo,periode_id=7,id_map={1:"42"},grade_codes=codes)
    assert count==1 and repo.insert_row.call_args.args[0].grade=="CNE"
    assert mysql.fetchall.return_value[0][1]=="CAPITAINE"


def test_gain_perte_unknown_grade_is_refused(codes):
    mysql=MagicMock();repo=MagicMock()
    mysql.fetchall.return_value=[("42","INCONNU","FICTIF","GAIN","TYPE","092026",None,None,1)]
    with pytest.raises(ValueError):
        access._write_gain_perte(MagicMock(),mysql,gp_repo=repo,periode_id=7,id_map={1:"42"},grade_codes=codes)
    repo.insert_row.assert_not_called()
