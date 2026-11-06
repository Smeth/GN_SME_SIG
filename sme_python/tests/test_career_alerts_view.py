from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication
from sme_gend.config.settings import Settings
from sme_gend.services.career_import_alerts import CareerAlert
from sme_gend.services.career_alerts_cache import CareerAlertsSnapshot
from sme_gend.ui.views import career_alerts_view as view


@pytest.fixture
def screen(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(view, "session_has_permission", lambda _: True)
    monkeypatch.setattr(view, "require_permission", lambda *_: True)
    def inline(parent, operation, success, *, on_error):
        try:
            result = operation()
        except Exception as error:
            on_error(error)
        else:
            success(result)
    monkeypatch.setattr(view, "run_in_background", inline)
    monkeypatch.setattr(view, "load_dossier_read_snapshot", lambda *_: {"prepared": True})
    widget = view.CareerAlertsView(Settings(database_path=Path("unused"), mysql_view_mois="2026-05"), MagicMock(), MagicMock())
    widget._loaded(CareerAlertsSnapshot(alerts=[
        CareerAlert("warning", "101", "Alice", "2026-01", "STRUCTURE_JUMP", "Structure A vers B"),
        CareerAlert("info", "202", "Bob", "", "GRADE_LONG_UNCHANGED", "Grade stable"),
    ], ready=True))
    yield widget
    widget.close()


def test_display_and_combined_filters(screen):
    assert screen.table.rowCount() == 2
    assert screen.table.item(0, 1).text() == "Alice"
    screen.search.setText("ALICE")
    assert screen.table.rowCount() == 1
    screen.severity.setCurrentIndex(2)
    assert screen.table.rowCount() == 0
    screen.search.clear()
    assert screen.table.item(0, 1).text() == "Bob"
    screen.severity.setCurrentIndex(0)
    screen.code.setCurrentIndex(screen.code.findData("STRUCTURE_JUMP"))
    assert screen.table.rowCount() == 1
    screen.month.setCurrentIndex(screen.month.findData(""))
    assert screen.table.rowCount() == 0
    screen.code.setCurrentIndex(0)
    assert screen.table.item(0, 1).text() == "Bob"


def test_open_selected_filtered_dossier(screen, monkeypatch):
    screen.search.setText("Bob")
    screen.table.selectRow(0)
    opener = MagicMock()
    monkeypatch.setattr(view, "open_personnel_dossier_dialog", opener)
    lookup = MagicMock()
    row = MagicMock(id="9876")
    lookup.resolve_dossier_for_legacy_id.return_value = (row, None)
    monkeypatch.setattr(view, "PersonnelLookupService", lambda _: lookup)
    screen._open()
    lookup.resolve_dossier_for_legacy_id.assert_called_once_with("202")
    screen._personnel.get_by_id.assert_not_called()
    assert opener.call_args.args[1] is row
    assert opener.call_args.kwargs["historical_context"] is None
    assert opener.call_args.kwargs["refresh_from_db"] is False
    assert opener.call_args.kwargs["allow_edit"] is False


def test_open_historical_fallback(screen, monkeypatch):
    screen.table.selectRow(0)
    lookup = MagicMock()
    row, context = MagicMock(), MagicMock()
    lookup.resolve_dossier_for_legacy_id.return_value = (row, context)
    monkeypatch.setattr(view, "PersonnelLookupService", lambda _: lookup)
    opener = MagicMock()
    monkeypatch.setattr(view, "open_personnel_dossier_dialog", opener)
    screen._open()
    lookup.resolve_dossier_for_legacy_id.assert_called_once_with("101")
    assert opener.call_args.kwargs["historical_context"] is context


@pytest.mark.parametrize("note", ["", "Vérifié dans le dossier"])
def test_acknowledge(screen, monkeypatch, note):
    screen.table.selectRow(0)
    monkeypatch.setattr(view.QInputDialog, "getMultiLineText", lambda *_: (note, True))
    ack = MagicMock()
    monkeypatch.setattr(view, "acknowledge_alert", ack)
    monkeypatch.setattr(screen, "reload", MagicMock())
    monkeypatch.setattr(view.cache, "invalidate_career_alerts_cache", MagicMock())
    screen._ack()
    assert ack.call_args.args[1:] == ("101", "STRUCTURE_JUMP", "2026-01")
    expected = CareerAlert("warning", "101", "Alice", "2026-01", "STRUCTURE_JUMP", "Structure A vers B")
    assert ack.call_args.kwargs == {"note": note, "fingerprint": expected.fingerprint(), "alert": expected}
    screen.reload.assert_called_once()


def test_cancel_denial_and_failure_preserve_alert(screen, monkeypatch):
    screen.table.selectRow(0)
    ack = MagicMock()
    monkeypatch.setattr(view, "acknowledge_alert", ack)
    monkeypatch.setattr(view.QInputDialog, "getMultiLineText", lambda *_: ("", False))
    screen._ack()
    ack.assert_not_called()
    monkeypatch.setattr(view, "require_permission", lambda *_: False)
    screen._ack()
    ack.assert_not_called()
    monkeypatch.setattr(view, "require_permission", lambda *_: True)
    monkeypatch.setattr(view.QInputDialog, "getMultiLineText", lambda *_: ("", True))
    monkeypatch.setattr(view.QMessageBox, "warning", MagicMock())
    ack.side_effect = RuntimeError("Stockage indisponible")
    screen._ack()
    assert screen.table.rowCount() == 2


def test_navigation():
    from sme_gend.ui import navigation_pages as pages
    from sme_gend.ui.sidebar_navigation import default_nav_groups, page_nav_icons
    from sme_gend.ui.access_control import PAGE_PERMISSION
    from sme_gend.ui.views.dashboard_stats_panel import _KPI_PAGE_MAP
    assert _KPI_PAGE_MAP["career_alerts"] == pages.PAGE_CAREER_ALERTS
    assert PAGE_PERMISSION[pages.PAGE_CAREER_ALERTS] == "effectifs.liste"
    assert any(leaf.page_index == pages.PAGE_CAREER_ALERTS for group in default_nav_groups() for leaf in group.items)
    assert len(page_nav_icons(QApplication.instance().style())) == pages.PAGE_COUNT


def test_ack_service_transaction_actor_and_audit(monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import career_import_alerts as service
    from sme_gend.services import shared_reference_write, auth_guard, auth_context, business_audit
    events = []
    @contextmanager
    def transaction(*args, **kwargs):
        events.append("begin")
        try:
            yield
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")
    monkeypatch.setattr(shared_reference_write, "shared_data_write", transaction)
    monkeypatch.setattr(shared_reference_write, "mark_shared_data_write", lambda _: events.append("revision"))
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda _: True)
    monkeypatch.setattr(auth_context, "resolve_audit_actor", lambda: "operator")
    audit = MagicMock(side_effect=lambda *a, **kw: events.append("audit"))
    monkeypatch.setattr(business_audit, "record_business_action", audit)
    connection = MagicMock()
    monkeypatch.setattr(service, "MySQLConnection", connection)
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-05")
    service.acknowledge_alert(settings, "101", "CODE", "", fingerprint="a" * 64, by="forged", note="  checked  ")
    cursor = connection.return_value.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    assert cursor.execute.call_args.args[1] == ("101", "CODE", "", "a" * 64, "operator", "checked")
    assert events == ["begin", "audit", "revision", "commit"]
    events.clear()
    audit.side_effect = RuntimeError("audit failed")
    with pytest.raises(RuntimeError):
        service.acknowledge_alert(settings, "101", "CODE", "", fingerprint="a" * 64)
    assert events == ["begin", "rollback"]
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda _: False)
    connection.reset_mock()
    with pytest.raises(PermissionError):
        service.acknowledge_alert(settings, "101", "CODE", "", fingerprint="a" * 64)
    connection.assert_not_called()
