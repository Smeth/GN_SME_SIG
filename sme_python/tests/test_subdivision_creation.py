from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFont, QFontDatabase
from sme_gend.services.subdivision_creation import validate_creation_path, create_path
from sme_gend.ui.subdivision_create_dialog import SubdivisionCreateDialog


@pytest.fixture
def snapshot():
    return NS(parts=(((1,None,'ROOT','Racine',None),(2,1,'U','Unité',None),
                      (3,2,'D','Division',None),(4,3,'T','Section',None),
                      (5,1,'U2','Autre unité',None)), ()))


def new(code):
    return dict(code=code, libelle='Libellé ' + code)


@pytest.mark.parametrize('path', [
    [2,3,4], [2,3,new('T2')], [2,new('D2'),new('T2')],
    [new('U3'),new('D3'),new('T3')]])
def test_partial_or_complete_path_can_be_created(snapshot, path):
    cursor = Mock(lastrowid=100)
    leaf = create_path(cursor, snapshot, path)
    assert leaf == (4 if path == [2,3,4] else 100)
    assert cursor.execute.call_count == sum(isinstance(item,dict) for item in path)


@pytest.mark.parametrize('path', [
    [2,3], [5,3,4], [2,4,4], [new('U'),new('D2'),new('T2')],
    [new('U3'),3,4], [new(''),new('D'),new('T')],
    [True,3,4], [2,new('x'*65),new('T')]])
def test_invalid_paths_make_no_writes(snapshot, path):
    cursor = Mock()
    with pytest.raises(ValueError):
        create_path(cursor, snapshot, path)
    cursor.execute.assert_not_called()


@pytest.fixture
def app():
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    yield app
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_dialog_allows_new_hierarchy_from_empty_database(app, tmp_path):
    view = SubdivisionCreateDialog(NS(parts=((), ())))
    for code, (combo, code_field, label, _) in zip(('U','D','T'), view._levels_ui):
        assert combo.currentData() is None
        code_field.setText(code)
        label.setText('Nouveau ' + code)
    path, label = view.values()
    assert len(path) == 3 and label == 'Nouveau T'
    view.resize(660, 640)
    view.show()
    app.processEvents()
    assert view.width() == 660
    assert view.height() <= 720
    assert view.grab().save(str(tmp_path / 'new_hierarchy.png'))
    view.accept()
    assert view.result() == QDialog.DialogCode.Accepted
    view.deleteLater()


def test_dialog_cascades_existing_levels_and_preserves_custom_label(app, snapshot):
    view = SubdivisionCreateDialog(snapshot, label='Ma subdivision')
    for node_id, (combo, _, _, _) in zip((2,3,4), view._levels_ui):
        combo.setCurrentIndex(combo.findData(node_id))
    assert view.values() == ([2,3,4], 'Ma subdivision')
    view._levels_ui[0][0].setCurrentIndex(view._levels_ui[0][0].findData(5))
    assert view._levels_ui[1][0].findData(3) == -1
    view.reject()
    assert view.result() == QDialog.DialogCode.Rejected
    view.deleteLater()


def test_dialog_refuses_incomplete_creation(app, snapshot, monkeypatch):
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    view = SubdivisionCreateDialog(snapshot)
    view.accept()
    warning.assert_called_once()
    assert view.result() != QDialog.DialogCode.Accepted
    view.deleteLater()
