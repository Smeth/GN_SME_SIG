from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QMessageBox, QPushButton

from sme_gend.ui import sme_session_dialog as dialog
from sme_gend.services import active_month_guard
from sme_gend.services import auth_guard


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_startup_recovery_requires_admin_before_showing_mutation(app, monkeypatch):
    months=(("2026-09",("SME-202609",)),("2026-10",("SME-202610",)))
    monkeypatch.setattr(active_month_guard,"list_open_months_for_recovery",lambda settings:months)
    monkeypatch.setattr(auth_guard,"session_has_admin_role",lambda:False)
    critical=MagicMock();monkeypatch.setattr(QMessageBox,"critical",critical)
    recovery=MagicMock();monkeypatch.setattr(active_month_guard,"recover_multiple_open_months",recovery)
    assert dialog.run_startup_open_month_recovery(None,object()) is False
    critical.assert_called_once();recovery.assert_not_called()


def test_startup_recovery_uses_explicit_selection_and_confirmed_display_snapshot(app, monkeypatch):
    months=(("2026-09",("SME-202609",)),("2026-10",("SME-202610",)))
    monkeypatch.setattr(active_month_guard,"list_open_months_for_recovery",lambda settings:months)
    monkeypatch.setattr(auth_guard,"session_has_admin_role",lambda:True)
    monkeypatch.setattr(QMessageBox,"question",lambda *a,**kw:QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox,"information",MagicMock())
    recovery=MagicMock(return_value={"active_month":"2026-10","closed_months":("2026-09",),"rows_closed":1,"already_resolved":False})
    monkeypatch.setattr(active_month_guard,"recover_multiple_open_months",recovery)
    def execute(dlg):
        combo=dlg.findChild(QComboBox)
        combo.setCurrentIndex(2)
        next(button for button in dlg.findChildren(QPushButton)
             if button.text()=="Garder ce mois et fermer les autres").click()
        return dlg.result()
    monkeypatch.setattr(QDialog,"exec",execute)
    settings=object()
    assert dialog.run_startup_open_month_recovery(None,settings) is True
    recovery.assert_called_once_with(settings, "2026-10", expected_open_months=["2026-09","2026-10"])
