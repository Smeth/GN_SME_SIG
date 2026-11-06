from types import SimpleNamespace
from unittest.mock import Mock
from datetime import datetime
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication,QWidget,QDialog,QLineEdit,QComboBox,QMessageBox
from sme_gend.ui.views.edition_sme_view import EditionSmeView
from sme_gend.ui.widgets.date_input import FrenchDateEdit


def test_group_grade_dialog_passes_optional_reference(monkeypatch):
    app=QApplication.instance() or QApplication([])
    parent=QWidget()
    parent._require_editable=lambda:True
    parent._get_active_src_rows=lambda:[0,1]
    model=Mock()
    model._grade_rows=[]
    model.grades=['ADM']
    model._rows=[SimpleNamespace(grade='MDL'),SimpleNamespace(grade='MDL')]
    model.bulk_set_grade.return_value=(2,0,[])
    parent._model=model
    parent._lbl_status=Mock()
    parent._bulk_result_message=lambda *args:'saved'
    parent._update_count=Mock()
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox,'information',Mock())
    def complete():
        for dialog in app.topLevelWidgets():
            if isinstance(dialog,QDialog) and dialog.windowTitle().startswith('Changer le grade'):
                dialog.findChild(QComboBox).setCurrentText('ADM')
                dialog.findChild(FrenchDateEdit).set_from_text('01/09/2026')
                reference=next(w for w in dialog.findChildren(QLineEdit) if w.placeholderText().startswith('R\u00e9f\u00e9rence de'))
                reference.setText('OG / DECRET 42')
                dialog.accept()
    QTimer.singleShot(0,complete)
    EditionSmeView._action_bulk_grade(parent)
    args,kwargs=model.bulk_set_grade.call_args
    assert args==([0,1],'ADM',datetime(2026,9,1))
    assert kwargs=={'reference':'OG / DECRET 42'}
    parent.close()


def test_grade_and_reference_share_one_undo_entry():
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    from test_agent_dossier_projection import _row
    app=QApplication.instance() or QApplication([])
    model=_EditionModel()
    row=_row()
    model.load([row],None,[],[],[],[])
    model._persist_update=lambda *a:None
    model._after_db_mutation=lambda:None
    count,unchanged,errors=model.bulk_set_grade([0],'ADM',datetime(2026,9,1),reference='OG 42')
    assert (count,unchanged,errors)==(1,0,[])
    assert len(model._undo_stack)==1
    entry=model._undo_stack[0]
    assert entry.field=='grade' and entry.extra_new['ndecision']=='OG 42'
    assert entry.extra_new['date_grade']==datetime(2026,9,1)
    app.processEvents()

