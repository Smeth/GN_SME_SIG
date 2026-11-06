"""La suppression ne démarre qu'après confirmation exacte de la période."""
from unittest.mock import MagicMock

import pytest

from sme_gend.ui.empty_month_dialog import run_empty_month_dialog


@pytest.mark.parametrize("text,accepted,should_run", [
    ("2026-09", True, True), ("2026-08", True, False), ("2026-09", False, False),
])
def test_month_confirmation_and_screen_refresh(monkeypatch, text, accepted, should_run):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_admin_role", lambda: True)
    monkeypatch.setattr("sme_gend.ui.import_mois_dialog.pick_import_mois", lambda *a, **kw: "2026-09")
    monkeypatch.setattr("sme_gend.ui.empty_month_dialog.QInputDialog.getText", lambda *a: (text, accepted))
    monkeypatch.setattr("sme_gend.ui.empty_month_dialog.QMessageBox.information", lambda *a: None)
    monkeypatch.setattr("sme_gend.ui.empty_month_dialog.QInputDialog.getItem", lambda *a: (a[3][0], True))
    monkeypatch.setattr("sme_gend.services.empty_sme_month.preview_empty_sme_month", lambda *a, **kw: {"carriere_situation": 3})
    service = MagicMock(return_value={"backup": "backup.zip", "warnings": []})
    monkeypatch.setattr("sme_gend.services.empty_sme_month.empty_sme_month", service)
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress", lambda parent, title, label, fn: fn())
    parent, settings = MagicMock(), object()
    assert run_empty_month_dialog(parent, settings) == should_run
    if should_run:
        service.assert_called_once_with(settings, "2026-09", imported_only=True)
        parent.window().refresh_for_month_change.assert_called_once()
    else:
        service.assert_not_called()
        parent.window().refresh_for_month_change.assert_not_called()
