from datetime import datetime
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
import sys

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from sme_gend.ui import publication_registry_dialog as ui


@pytest.fixture(scope="module")
def app():
    from PySide6.QtGui import QFont, QFontDatabase
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setFont(QFont("Segoe UI", 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, "light")
    return app


@pytest.fixture
def records(monkeypatch):
    registry = ModuleType("sme_gend.services.sme_publication_registry")
    values = [SimpleNamespace(id=1, revision_no=1, revision_id="revision1", mois="2026-09", status="valide",
        version=2, created_at=datetime(2026,9,30), created_by="author", validated_at=None, validated_by="admin",
        replaced_by_id=None, validation_note="")]
    registry.list_publications = Mock(return_value=values)
    registry.verify_publication = Mock(return_value={"verified": True})
    monkeypatch.setitem(sys.modules, registry.__name__, registry)
    monkeypatch.setattr(ui, "run_in_background", lambda parent, work, done, **kw: done(work()))
    monkeypatch.setattr(ui, "run_with_progress", lambda parent, title, message, work, **kw: work())
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    monkeypatch.setattr(QMessageBox, "information", Mock())
    monkeypatch.setattr(ui, "require_permission", lambda *a, **kw: True)
    return registry, values


def test_registry_reader_sees_versions_and_verify_but_cannot_validate_or_download(app, monkeypatch, records):
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: permission in {"publications.view", "publications.verify"})
    view = ui.PublicationRegistryDialog(object(), month="2026-09")
    view._table.selectRow(0)
    assert view._verify.isEnabled()
    assert not view._validate.isEnabled() and not view._replace.isEnabled() and not view._download.isEnabled()
    assert view._table.rowCount() == 1
    records[0].list_publications.assert_called_once_with(view._settings, mois="2026-09", status=None, limit=101, offset=0)
    view._on_verify()
    records[0].verify_publication.assert_called_once_with(view._settings, 1)
    view.close()


def test_download_requires_both_dedicated_right_and_export(app, monkeypatch, records):
    permissions = {"publications.view", "publications.download"}
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: permission in permissions)
    view = ui.PublicationRegistryDialog(object())
    view._table.selectRow(0)
    assert not view._download.isEnabled()
    permissions.add("effectifs.export")
    view._update_actions()
    assert view._download.isEnabled()
    view.close()


def test_revoked_view_permission_clears_rows_after_pending_reload(app, monkeypatch, records):
    allowed = {"value": True}
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: allowed["value"])
    callbacks = []
    monkeypatch.setattr(ui, "run_in_background", lambda parent, work, done, **kw: callbacks.append((work, done)))
    view = ui.PublicationRegistryDialog(object())
    work, done = callbacks[0]
    values = work()
    allowed["value"] = False
    done(values)
    assert view._records == [] and view._table.rowCount() == 0
    assert not view._download.isEnabled()
    view.close()


def test_validation_controls_apply_only_to_drafts(app, monkeypatch, records):
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: True)
    view = ui.PublicationRegistryDialog(object())
    view._table.selectRow(0)
    assert not view._validate.isEnabled() and not view._replace.isEnabled()
    records[1][0].status = "brouillon"
    view._update_actions()
    assert view._validate.isEnabled() and view._replace.isEnabled()
    view.close()


def test_table_remains_the_main_area_on_a_client_screen(app, monkeypatch, records, tmp_path):
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: True)
    view = ui.PublicationRegistryDialog(object())
    view.resize(1280, 720)
    view.show()
    app.processEvents()
    assert view._table.height() > view.height() * .65
    assert view._download.isVisible() and view._refresh.isVisible()
    screenshot = tmp_path / "publication_registry.png"
    assert view.grab().save(str(screenshot))
    print("Publication registry render:", screenshot)
    view.close()


def test_register_pages_without_discarding_month_filter(app, monkeypatch, records):
    monkeypatch.setattr(ui, "session_has_permission", lambda permission: True)
    records[0].list_publications.return_value = records[1] * 101
    view = ui.PublicationRegistryDialog(object(), month="2026-09")
    assert view._table.rowCount() == 100 and view._next.isEnabled()
    assert not view._previous.isEnabled()
    view._change_page(1)
    assert records[0].list_publications.call_args.kwargs == dict(mois="2026-09", status=None, limit=101, offset=100)
    assert view._previous.isEnabled()
    view._filter_changed()
    assert records[0].list_publications.call_args.kwargs["offset"] == 0
    view.close()
