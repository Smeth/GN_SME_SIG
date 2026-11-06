from unittest.mock import Mock
import pytest
from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtWidgets import QApplication, QComboBox, QTableView, QMessageBox, QLabel
from sme_gend.ui.padmin_satellite_dialog import BulkMissionStageDialog, PadminSatelliteDialog
from sme_gend.ui.views.edition_sme_view import _EditionModel, _EditionDelegate, _COL_IDX
from test_edition_sme_editor import _row


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('kind', ['mission', 'stage_local', 'stage_ex', 'stage_interne'])
def test_bulk_nature_required_and_marked(app, kind):
    dialog = BulkMissionStageDialog(None, count=2)
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData(kind))
    dialog._on_ok()
    assert dialog._result is None
    assert QMessageBox.warning.call_count == 1
    assert any(label.text() == 'Nature *' for label in dialog.findChildren(QLabel))
    field = dialog._mn if kind == 'mission' else dialog._sn.lineEdit()
    field.setText('NATURE TEST')
    dialog._on_ok()
    assert dialog._result is not None


@pytest.mark.parametrize('kind', ['mission', 'stage_local', 'stage_ex', 'stage_interne'])
def test_individual_nature_required(app, kind):
    dialog = PadminSatelliteDialog(None, kind=kind, agent_label='TEST')
    dialog._on_ok()
    assert dialog._result is None
    dialog._f1.setText('NATURE TEST')
    dialog._on_ok()
    assert dialog._result is not None


def test_bulk_malade_date_required_before_accepting(app):
    dialog = BulkMissionStageDialog(None, count=2)
    dialog._on_ok()
    assert dialog._result is None
    dialog._md.set_from_text('2026-11-06')
    dialog._on_ok()
    assert dialog._result[1].malade.date_constat


@pytest.mark.parametrize('proxy', [False, True])
@pytest.mark.parametrize('accept', [False, True])
def test_position_selection_opens_prompt_once_without_enter(app, proxy, accept):
    model = _EditionModel()
    model.load([_row()], None, [], [], [], ['Présent', 'Mission'])
    model._persist_update = Mock()
    model.position_change_prompt = Mock(return_value=accept)
    view = QTableView()
    display = QSortFilterProxyModel() if proxy else model
    if proxy: display.setSourceModel(model)
    view.setModel(display)
    delegate = _EditionDelegate(model, view)
    view.setItemDelegate(delegate)
    view.show()
    index = display.index(0, _COL_IDX['position_admin'])
    view.edit(index)
    app.processEvents()
    editor = view.findChild(QComboBox)
    assert editor is not None
    model.position_change_prompt.assert_not_called()
    editor.setCurrentIndex(editor.findText('Mission'))
    model.position_change_prompt.assert_not_called()
    editor.activated.emit(editor.currentIndex())
    app.processEvents()
    assert model.position_change_prompt.call_count == 1
    assert model._rows[0].position_admin == ('Mission' if accept else 'Présent')
    assert model._persist_update.call_count == (1 if accept else 0)
    view.close()


def test_modal_position_prompt_does_not_reenter_on_focus_loss(app):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout
    model = _EditionModel()
    model.load([_row()], None, [], [], [], ['Présent', 'Mission'])
    view = QTableView()
    view.setModel(model)
    view.setItemDelegate(_EditionDelegate(model, view))
    prompts = []
    def prompt(*args):
        prompts.append(args[-1])
        if len(prompts) > 1:
            return False
        dialog = QDialog(view)
        QVBoxLayout(dialog).addWidget(QLineEdit())
        QTimer.singleShot(0, dialog.reject)
        dialog.exec()
        return False
    model.position_change_prompt = prompt
    view.show()
    view.edit(model.index(0, _COL_IDX['position_admin']))
    app.processEvents()
    editor = view.findChild(QComboBox)
    editor.setCurrentIndex(editor.findText('Mission'))
    editor.activated.emit(editor.currentIndex())
    for _ in range(3): app.processEvents()
    assert prompts == ['Mission']
    assert model._rows[0].position_admin == 'Présent'
    view.close()
