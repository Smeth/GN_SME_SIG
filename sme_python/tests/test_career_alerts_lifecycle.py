from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from unittest.mock import MagicMock
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from sme_gend.services import career_alerts_cache as cache
from sme_gend.services.career_import_alerts import CareerAcknowledgement, CareerAlert
from sme_gend.ui.views import career_alerts_view as view
from test_career_alerts_view import screen


def test_dashboard_counts_information_and_warning(screen, monkeypatch):
    from sme_gend.services.dashboard_service import build_career_alerts_kpi_card
    snapshot = cache.CareerAlertsSnapshot(total=2, warning_count=1, ready=True)
    monkeypatch.setattr(cache, "get_career_alerts_snapshot", lambda _: snapshot)
    card = build_career_alerts_kpi_card(screen._settings, scan_if_missing=False)
    assert card.value == "2"
    assert "1 avertissement" in card.subtitle and "1 information" in card.subtitle
    snapshot.total = 1
    snapshot.warning_count = 0
    assert build_career_alerts_kpi_card(screen._settings, scan_if_missing=False).value == "1"


def test_explicit_reference_filter(screen):
    current = CareerAlert("warning", "303", "Current", "", "MATRICULE_DUPLIQUE", "Shared")
    screen._loaded(cache.CareerAlertsSnapshot(alerts=screen._alerts + [current], ready=True))
    screen.reference.setCurrentIndex(screen.reference.findData("current"))
    assert screen._visible == [current]
    screen.reference.setCurrentIndex(screen.reference.findData("historical"))
    assert current not in screen._visible
    assert "hors historique" in current.reference_label


def test_read_and_mutation_are_dispatched_and_controls_blocked(screen, monkeypatch):
    queued = []
    monkeypatch.setattr(view, "run_in_background", lambda parent, operation, success, **kw: queued.append((operation, success, kw["on_error"])))
    screen.table.selectRow(0)
    ack = MagicMock()
    monkeypatch.setattr(view, "acknowledge_alert", ack)
    monkeypatch.setattr(view.QInputDialog, "getMultiLineText", lambda *_: ("checked", True))
    screen._ack()
    assert screen._busy
    ack.assert_not_called()
    assert not screen.ack.isEnabled() and not screen.open.isEnabled()
    screen._ack()
    assert len(queued) == 1
    queued[0][0]()
    ack.assert_called_once()


def test_late_delivery_after_month_change_is_discarded(screen, monkeypatch):
    queued = []
    monkeypatch.setattr(view, "run_in_background", lambda parent, operation, success, **kw: queued.append((operation, success)))
    screen.reload()
    first_operation, first_success = queued[0]
    screen._settings = replace(screen._settings, mysql_view_mois="2026-06")
    screen.mark_dirty()
    first_success(cache.CareerAlertsSnapshot(alerts=[CareerAlert("warning", "OLD", "Old", "", "CODE", "old")], ready=True))
    assert len(queued) == 2
    assert all(a.agent_legacy_id != "OLD" for a in screen._alerts)
    queued[1][1](cache.CareerAlertsSnapshot(alerts=[], ready=True))
    assert screen._display_mois == "2026-06"


def test_history_search_metadata_legacy_and_reactivation(screen, monkeypatch):
    monkeypatch.setattr(screen, "reload", MagicMock())
    screen.state.setCurrentIndex(1)
    record = CareerAcknowledgement("101", "CODE", "2026-03", "a" * 64, "operator", "2026-04-01 12:00:00", "verified", "token", "Alice")
    legacy = replace(record, fingerprint=None, note="legacy")
    screen._loaded_history([record, legacy])
    screen.search.setText("verified")
    assert screen.table.rowCount() == 1
    assert screen.table.item(0, 4).text() == "operator"
    screen.table.selectRow(0)
    monkeypatch.setattr(view, "confirm_yes_no", lambda *_: True)
    reopen = MagicMock()
    monkeypatch.setattr(view, "reopen_career_alert", reopen)
    screen._ack()
    assert reopen.call_args.args[1] == record
    screen.search.setText("legacy")
    screen.table.selectRow(0)
    assert not screen.ack.isEnabled()


def test_inflight_scan_is_shared_and_returns_complete_result(screen, monkeypatch):
    cache.invalidate_career_alerts_cache()
    entered, release = Event(), Event()
    follower_waiting = Event()
    class Completion:
        def __init__(self):
            self.event = Event()
        def wait(self, timeout=None):
            follower_waiting.set()
            return self.event.wait(timeout)
        def set(self):
            self.event.set()
    monkeypatch.setattr(cache, "Event", Completion)
    result = CareerAlert("info", "101", "Alice", "", "CODE", "checked")
    def scan(*_, **kw):
        entered.set()
        assert release.wait(5)
        return [result]
    scanner = MagicMock(side_effect=scan)
    monkeypatch.setattr(cache, "scan_all_career_alerts", scanner)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(cache.scan_and_cache_career_alerts, screen._settings)
        assert entered.wait(5)
        second = workers.submit(cache.scan_and_cache_career_alerts, screen._settings)
        assert follower_waiting.wait(5)
        release.set()
        assert first.result(timeout=5).alerts == [result]
        assert second.result(timeout=5).alerts == [result]
    assert scanner.call_count == 1
    cache.invalidate_career_alerts_cache()


def test_invalidated_scan_cannot_repopulate_cache(screen, monkeypatch):
    cache.invalidate_career_alerts_cache()
    entered, release = Event(), Event()
    calls = []
    def scan(*_, **kw):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            return [CareerAlert("info", "OLD", "Old", "", "CODE", "old")]
        return [CareerAlert("info", "NEW", "New", "", "CODE", "fresh")]
    monkeypatch.setattr(cache, "scan_all_career_alerts", scan)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(cache.scan_and_cache_career_alerts, screen._settings)
        assert entered.wait(5)
        cache.invalidate_career_alerts_cache(screen._settings)
        release.set()
        pending.result(timeout=5)
    snapshot = cache.get_career_alerts_snapshot(screen._settings)
    assert snapshot.ready and [alert.agent_legacy_id for alert in snapshot.alerts] == ["NEW"]
    assert len(calls) == 2


def test_actual_worker_delivers_on_gui_thread(screen, monkeypatch):
    from sme_gend.ui.bg_task import run_in_background
    from time import monotonic
    app = QApplication.instance()
    worker_threads, delivery_threads = [], []
    def operation():
        worker_threads.append(QThread.currentThread())
        return "loaded"
    run_in_background(screen, operation, lambda _: delivery_threads.append(QThread.currentThread()))
    deadline = monotonic() + 5
    while not delivery_threads and monotonic() < deadline:
        app.processEvents()
    assert worker_threads and worker_threads[0] != app.thread()
    assert delivery_threads == [app.thread()]


def test_prepared_readonly_dossier_renders_without_mysql(screen, monkeypatch):
    from test_personnel_dossier_open import _row
    from sme_gend.ui import personnel_dossier_dialog as module
    from sme_gend.repositories.mysql_connection import MySQLConnection
    row = _row()
    snapshot = {
        "events": {name: [] for name in ("missions", "stages", "malades", "desertions", "conges")},
        "dossier": {name: [] for name in ("list_diplomes", "list_distinctions", "list_passations", "list_affectations")},
        "grades": [], "structures": [], "positions": [], "timeline": [], "situation": None, "alerts": [],
        "media": {"root": Path("unused"), "photo": None, "grade": None},
    }
    forbidden = MagicMock(side_effect=AssertionError("Database access in GUI thread"))
    monkeypatch.setattr(MySQLConnection, "connect", forbidden)
    monkeypatch.setattr(module, "AgentDossierEventsService", forbidden)
    monkeypatch.setattr(module, "AgentDossierService", forbidden)
    monkeypatch.setattr(module, "ensure_personnel_media_dirs", lambda: None)
    monkeypatch.setattr(module, "person_photo_path", lambda *_: None)
    monkeypatch.setattr(module, "grade_insignia_path", lambda *_: None)
    service = MagicMock(settings=screen._settings)
    dialog = module.PersonnelDossierDialog(None, row, MagicMock(), service,
                                         allow_edit=False, readonly_snapshot=snapshot)
    assert dialog._title.text() == row.noms
    forbidden.assert_not_called()
    service.get_by_id.assert_not_called()
    dialog.close()


def test_reactivation_permission_is_checked_before_storage(screen, monkeypatch):
    from sme_gend.services import career_import_alerts as service
    from sme_gend.services import auth_guard
    record = CareerAcknowledgement("101", "CODE", "", "a" * 64, "operator", "date", "note", "token")
    connection = MagicMock()
    monkeypatch.setattr(service, "MySQLConnection", connection)
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda _: False)
    with pytest.raises(PermissionError):
        service.reopen_career_alert(screen._settings, record)
    connection.assert_not_called()


def test_revoked_read_permission_discards_late_result(screen, monkeypatch):
    queued = []
    monkeypatch.setattr(view, "run_in_background", lambda parent, operation, success, **kw: queued.append(success))
    screen.reload()
    monkeypatch.setattr(view, "session_has_permission", lambda _: False)
    queued[0](cache.CareerAlertsSnapshot(alerts=screen._alerts, ready=True))
    assert screen.table.rowCount() == 0
    assert not screen.open.isEnabled()


def test_oversized_comment_never_starts_write(screen, monkeypatch):
    screen.table.selectRow(0)
    monkeypatch.setattr(view.QInputDialog, "getMultiLineText", lambda *_: ("x" * 256, True))
    monkeypatch.setattr(view.QMessageBox, "warning", MagicMock())
    operation = MagicMock()
    monkeypatch.setattr(view, "run_in_background", operation)
    screen._ack()
    operation.assert_not_called()
