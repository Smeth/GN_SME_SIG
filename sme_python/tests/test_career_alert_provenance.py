from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import career_alert_provenance as service


@pytest.fixture
def evidence(monkeypatch):
    connection = MagicMock()
    cursor = connection.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    rows = {
        "agents": [("101", "SAME NAME", datetime(2026, 10, 1), datetime(2026, 10, 2)),
                   ("202", "SAME NAME", datetime(2026, 10, 1), datetime(2026, 10, 2))],
        "careers": [("101", 11, "2026-01", datetime(2026, 10, 1), 7),
                    ("101", 12, "2026-09", datetime(2026, 10, 1), None),
                    ("202", 21, "2026-01", datetime(2026, 10, 1), 7)],
        "visible": [(12, "101")],
        "imports": [(7, r"C:\synthetic\january.mdb", datetime(2026, 10, 1, 12))],
        "audit": [(1, datetime(2026, 10, 1), "create", "operator", "101", "2026-01",
                   {"after": {"access_join_id": "101"}, "edit_motif": "Synthetic creation"})],
    }
    current = [None]
    def execute(sql, params):
        if sql.startswith("SELECT legacy_access_id,noms"):
            current[0] = "agents"
        elif sql.startswith("SELECT a.legacy_access_id,c.id"):
            current[0] = "careers"
        elif sql.startswith("SELECT id,chemin_fichier"):
            current[0] = "imports"
        elif sql.startswith("SELECT id,ts_utc"):
            current[0] = "audit"
        else:
            current[0] = "visible"
        if isinstance(rows[current[0]], Exception):
            raise rows[current[0]]
    cursor.execute.side_effect = execute
    cursor.fetchall.side_effect = lambda: rows[current[0]]
    monkeypatch.setattr(service, "MySQLConnection", lambda _: connection)
    monkeypatch.setattr(service, "session_has_permission", lambda _: True)
    monkeypatch.setattr(service, "resolve_carriere_scope_mois", lambda _: "2026-09")
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    return settings, rows, connection, cursor


def test_months_creation_and_import_are_distinct_facts(evidence):
    settings, rows, connection, cursor = evidence
    report = service.load_career_provenance(settings, ["101", "202"])
    assert report.fiches[0].months == ["2026-01", "2026-09"]
    assert report.fiches[0].created.startswith("2026-10-01")
    assert report.fiches[0].visible_in_month is True
    assert report.fiches[1].visible_in_month is False
    assert "seule la fiche 101 y figure" in report.presence_text()
    assert "N’y figurent pas : 202" in report.presence_text()
    assert sum(event.kind == "Situation liée à un import" for event in report.events) == 2
    creation = next(event for event in report.events if event.kind == "Création journalisée")
    assert creation.actor == "operator" and "Synthetic creation" in creation.detail
    text = service.describe_provenance(report)
    assert "n’est pas une date de création" in text
    assert "january.mdb" in text and "cause reste inconnue" in text
    connection.connect.assert_called_once_with(read_only=True)
    assert all(call.args[0].lstrip().upper().startswith("SELECT") for call in cursor.execute.call_args_list)


def test_two_fiches_actually_visible_in_same_month(evidence):
    settings, rows, *_ = evidence
    rows["visible"] = [(12, "101"), (22, "202")]
    report = service.load_career_provenance(settings, ["101", "202"])
    assert "2 de ces fiches" in report.presence_text()
    assert "ensemble dans la liste de ce mois" in report.presence_text()


def test_numeric_audit_collision_does_not_prove_origin():
    targets, confirmed = service._audit_target({}, "52", {"52", "23015"}, {"52": {"23015"}})
    assert targets == ["23015", "52"] and not confirmed
    assert service._audit_target({"after": {"access_join_id": "52"}}, "52", {"52", "23015"}, {"52": {"23015"}}) == (["52"], True)
    assert service._audit_target({"after": {"access_join_id": "unrelated"}}, "52", {"52"}, {}) == ([], True)


def test_unavailable_audit_is_not_reported_as_absent_history(evidence):
    settings, rows, *_ = evidence
    rows["audit"] = RuntimeError("Unavailable")
    report = service.load_career_provenance(settings, ["101", "202"])
    assert report.warnings and "Journal d’audit indisponible" in report.warnings[0]
    assert "LECTURES INCOMPLÈTES" in service.describe_provenance(report)
    assert report.fiches[0].visible_in_month


def test_missing_fiche_and_unavailable_import_are_explicit(evidence):
    settings, rows, *_ = evidence
    rows["imports"] = RuntimeError("Unavailable")
    report = service.load_career_provenance(settings, ["101", "202", "999"])
    assert report.missing_ids == ["999"]
    assert "introuvables" in report.presence_text()
    assert "lots d’import indisponible" in report.warnings[0]


def test_no_permission_means_no_database_access(monkeypatch):
    monkeypatch.setattr(service, "session_has_permission", lambda _: False)
    connection = MagicMock()
    monkeypatch.setattr(service, "MySQLConnection", connection)
    with pytest.raises(PermissionError):
        service.load_career_provenance(object(), ["101"])
    connection.assert_not_called()


def test_dialog_loads_evidence_asynchronously_and_ignores_late_delivery(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui import career_alert_details_dialog as ui
    from sme_gend.services.career_import_alerts import CareerAlert
    app = QApplication.instance() or QApplication([])
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    widget = ui.CareerAlertDetailsDialog(None, CareerAlert("warning", "101", "Example", "", "CODE", "Test"),
                                       settings, MagicMock(), MagicMock())
    queued = []
    monkeypatch.setattr(ui, "require_permission", lambda *_: True)
    loader = MagicMock(return_value=service.CareerProvenance("2026-09", [], []))
    monkeypatch.setattr(ui, "load_career_provenance", loader)
    monkeypatch.setattr(ui, "run_in_background", lambda parent, operation, success, **kw: queued.append((operation, success)))
    widget.trace_tabs.setCurrentIndex(1)
    loader.assert_not_called()
    assert widget._trace_loading and not widget.trace_refresh.isEnabled()
    result = queued[0][0]()
    loader.assert_called_once_with(settings, ["101"])
    widget.reject()
    queued[0][1](result)
    assert not widget._trace_loaded
