from pathlib import Path
from datetime import date
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services import personnel_merge_service as service
from sme_gend.services.career_import_alerts import RelatedCareerAgent
from sme_gend.ui import personnel_merge_dialog as ui


@pytest.fixture
def dialog(monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    widget = ui.PersonnelMergeDialog(None, settings, [RelatedCareerAgent("23015", "EXAMPLE", date(1970, 1, 1), "MIL", "SOLD"),
        RelatedCareerAgent("52", "EXAMPLE", date(1970, 1, 1), "MIL", "SOLD")], "23015")
    def inline(parent, operation, success, *, on_error):
        try:
            value = operation()
        except Exception as error:
            on_error(error)
        else:
            success(value)
    monkeypatch.setattr(ui, "run_in_background", inline)
    yield widget
    widget.close()


def preview(conflicts=()):
    return service.PersonnelMergePreview("52", "23015", "EXAMPLE", "EXAMPLE",
        {"carriere_situation":1, "agent_diplome":2}, ("2026-01",), conflicts, (), {}, "token", ("localhost",3306,"test"))


def test_preview_and_explicit_verification_required_before_apply(dialog, monkeypatch):
    loader, writer = MagicMock(return_value=preview()), MagicMock()
    monkeypatch.setattr(ui, "preview_personnel_merge", loader)
    monkeypatch.setattr(ui, "apply_personnel_merge", writer)
    assert dialog.target.currentData() == "23015" and dialog.source.currentData() == "52"
    assert not dialog.apply_button.isEnabled()
    dialog._load_preview()
    assert "2026-01" in dialog.summary.toPlainText()
    assert "Situations mensuelles" in dialog.summary.toPlainText()
    assert not dialog.apply_button.isEnabled()
    dialog.reason.setText("Identité et pièces vérifiées")
    dialog.confirmed.setChecked(True)
    assert dialog.apply_button.isEnabled()
    monkeypatch.setattr(ui, "confirm_yes_no", lambda *_: False)
    dialog._apply()
    writer.assert_not_called()
    dialog.target.setCurrentIndex(1)
    assert not dialog.apply_button.isEnabled()


def test_blocking_conflict_never_enables_transfer(dialog, monkeypatch):
    monkeypatch.setattr(ui, "preview_personnel_merge", lambda *_: preview(("Même période 2026-01",)))
    dialog._load_preview()
    dialog.reason.setText("Verified")
    dialog.confirmed.setChecked(True)
    assert not dialog.apply_button.isEnabled()


def test_ui_confirms_exact_direction_and_uses_preview_token(dialog, monkeypatch):
    plan = preview()
    monkeypatch.setattr(ui, "preview_personnel_merge", lambda *_: plan)
    writer = MagicMock(return_value={"source":"52","target":"23015","backup":"synthetic.zip"})
    monkeypatch.setattr(ui, "apply_personnel_merge", writer)
    confirmation = MagicMock(return_value=True)
    monkeypatch.setattr(ui, "confirm_yes_no", confirmation)
    monkeypatch.setattr(ui.QMessageBox, "information", MagicMock())
    dialog._load_preview()
    dialog.reason.setText("Verified")
    dialog.confirmed.setChecked(True)
    dialog._apply()
    assert "52" in confirmation.call_args.args[2] and "23015" in confirmation.call_args.args[2]
    assert writer.call_args.args[1] is plan
    assert writer.call_args.kwargs["reason"] == "Verified"


def test_permission_denial_precedes_read_or_backup(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr(service, "MySQLConnection", connection)
    monkeypatch.setattr(service, "session_has_admin_role", lambda: False)
    with pytest.raises(PermissionError):
        service.preview_personnel_merge(object(), "52", "23015")
    with pytest.raises(PermissionError):
        service.apply_personnel_merge(object(), preview(), reason="Verified")
    connection.assert_not_called()


def test_original_photo_remains_available_under_old_identity(monkeypatch):
    from sme_gend.services import agent_identity_alias as aliases
    from sme_gend.services import personnel_media
    monkeypatch.setattr(aliases, "aliases_for", lambda *_: ["23015", "52"])
    old_photo = Path("synthetic/photos/52.jpg")
    monkeypatch.setattr(personnel_media, "person_photo_path", lambda key: old_photo if key == "52" else None)
    assert aliases.identity_photo_path(object(), "23015") == old_photo
    target_photo = Path("synthetic/photos/23015.jpg")
    monkeypatch.setattr(personnel_media, "person_photo_path", lambda _: target_photo)
    lookup = MagicMock()
    monkeypatch.setattr(aliases, "aliases_for", lookup)
    assert aliases.identity_photo_path(object(), "23015") == target_photo
    lookup.assert_not_called()


def test_admin_without_removal_permission_cannot_merge(monkeypatch):
    monkeypatch.setattr(service, "session_has_admin_role", lambda: True)
    monkeypatch.setattr(service, "session_can_modify_personnel", lambda: True)
    monkeypatch.setattr(service, "session_can_delete_personnel", lambda: False)
    connection = MagicMock()
    monkeypatch.setattr(service, "MySQLConnection", connection)
    with pytest.raises(PermissionError):
        service.preview_personnel_merge(object(), "52", "23015")
    connection.assert_not_called()
