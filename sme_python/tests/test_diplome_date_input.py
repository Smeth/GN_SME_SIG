from datetime import date

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from sme_gend.ui.diplome_edit_dialog import DiplomeEditDialog
from sme_gend.ui.widgets.date_input import FrenchDateEdit

app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize("text,expected", [
    ("07/27/2025", date(2025, 7, 27)),
    ("27/07/2025", date(2025, 7, 27)),
    ("07/08/2025", date(2025, 8, 7)),
    ("29/02/2024", date(2024, 2, 29)),
])
def test_diplome_accepts_date(text, expected):
    dialog = DiplomeEditDialog(None)
    dialog._lib.setText("Master")
    dialog._date.date_edit().setText(text)
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values()["date_obtention"] == expected
    assert dialog._date.date_edit().text() == expected.strftime("%d/%m/%Y")


@pytest.mark.parametrize("text,fragment", [
    ("", "obligatoire"), ("31/02/2025", "invalide"), ("27/07/20", "invalide"),
])
def test_diplome_rejects_missing_or_invalid_date(monkeypatch, text, fragment):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, msg: messages.append(msg))
    dialog = DiplomeEditDialog(None)
    dialog._lib.setText("Master")
    dialog._date.date_edit().setText(text)
    dialog._on_ok()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert fragment in messages[0]


def test_imported_american_date_is_normalized():
    field = FrenchDateEdit()
    field.set_from_text("07/27/2025")
    assert field.to_storage_str() == "2025-07-27"
    assert field.date_edit().text() == "27/07/2025"
