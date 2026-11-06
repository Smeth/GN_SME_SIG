from datetime import datetime
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QEvent,Qt,QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication,QWidget,QDialog,QLineEdit,QLabel
from PySide6.QtTest import QTest
from sme_gend.ui.widgets.date_input import FrenchDateEdit
from sme_gend.ui.views.edition_sme_view import EditionSmeView
from sme_gend.services.grade_nomination import GradeNomination
from test_agent_dossier_projection import _row


@pytest.fixture(scope='module')
def app():return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('key,text',[(Qt.Key.Key_Slash,'/'),(Qt.Key.Key_A,'a'),(Qt.Key.Key_Period,'.'),(Qt.Key.Key_NumLock,''),(Qt.Key.Key_Shift,''),(Qt.Key.Key_Up,''),(Qt.Key.Key_Down,''),(Qt.Key.Key_Space,' ')])
def test_date_ignores_non_numeric_keys_without_crashing(app,key,text):
    date=FrenchDateEdit();date.set_from_text('01/07/2026')
    event=QKeyEvent(QEvent.Type.KeyPress,key,Qt.KeyboardModifier.NoModifier,text)
    date.date_edit().keyPressEvent(event)
    assert date.to_datetime()==datetime(2026,7,1)


@pytest.mark.parametrize('modifier',[Qt.KeyboardModifier.NoModifier,Qt.KeyboardModifier.KeypadModifier])
def test_date_supports_regular_and_numeric_keypad_digits(app,modifier):
    date=FrenchDateEdit()
    for digit in '01072026':
        date.date_edit().keyPressEvent(QKeyEvent(QEvent.Type.KeyPress,ord(digit),modifier,digit))
    assert date.to_datetime()==datetime(2026,7,1)


@pytest.mark.parametrize('reference',[None,'OG / DECRET 42'])
def test_individual_grade_dialog_has_reference_and_accepts_keyboard_date(app,reference):
    parent=QWidget();parent._model=SimpleNamespace(_grade_rows=[])
    captured=[]
    def complete():
        for dialog in app.topLevelWidgets():
            if isinstance(dialog,QDialog) and dialog.windowTitle().startswith('Nomination au grade'):
                date=dialog.findChild(FrenchDateEdit);date.set_empty()
                QTest.keyClicks(date.date_edit(),'01/07/2026')
                line=next(w for w in dialog.findChildren(QLineEdit) if w.placeholderText().startswith('R\u00e9f\u00e9rence'))
                assert any('Ref/OG/Decret' in w.text() for w in dialog.findChildren(QLabel))
                line.setText(reference or '')
                captured.append(True);dialog.accept()
    QTimer.singleShot(0,complete)
    result=EditionSmeView._prompt_grade_nomination_date(parent,_row(),'MDL','ADM')
    assert captured and result==GradeNomination(datetime(2026,7,1),reference)
    parent.close()
