from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services.career_import_alerts import _AgentIdentityRow, _scan_matricule_duplicates
from sme_gend.ui import career_alert_details_dialog as details


@pytest.fixture
def duplicate_alert():
    rows = [_AgentIdentityRow("101", "SAME NAME", "MIL123", "SOLDE456", date(1970, 1, 1)),
            _AgentIdentityRow("202", "SAME NAME", "MIL123", "SOLDE456", date(1970, 1, 1))]
    return _scan_matricule_duplicates(rows)[0]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_duplicate_names_are_identified_by_separate_references(duplicate_alert):
    assert "fiche 101" in duplicate_alert.message
    assert "SAME NAME [fiche 202]" in duplicate_alert.message
    assert [p.legacy_id for p in duplicate_alert.related_agents] == ["101", "202"]
    assert duplicate_alert.matched_field == "matricule militaire"
    assert duplicate_alert.matched_value == "MIL123"
    assert "Doublon potentiel de dossier" in duplicate_alert.message
    assert "ne supprimez pas" in duplicate_alert.resolution_guidance
    assert "ne corrige ni ne fusionne" in duplicate_alert.resolution_guidance


def test_comparison_displays_both_fiches_and_selects_other(app, duplicate_alert):
    widget = details.CareerAlertDetailsDialog(None, duplicate_alert,
        Settings(database_path=Path("unused"), mysql_view_mois="2026-09"), MagicMock(), MagicMock())
    assert widget.related_table.rowCount() == 2
    assert widget.related_table.item(0, 0).text() == "101"
    assert widget.related_table.item(1, 0).text() == "202"
    assert widget.related_table.item(1, 2).text() == "01/01/1970"
    assert widget.related_table.item(1, 3).text() == "MIL123"
    assert widget.related_table.currentRow() == 1
    assert "Liste générale" in widget.guidance.toPlainText()
    widget.close()


def test_open_other_fiche_uses_its_identity_not_alert_identity(app, duplicate_alert, monkeypatch):
    widget = details.CareerAlertDetailsDialog(None, duplicate_alert,
        Settings(database_path=Path("unused"), mysql_view_mois="2026-09"), MagicMock(), MagicMock())
    lookup = MagicMock()
    row, context = MagicMock(id="9999"), None
    lookup.resolve_dossier_for_legacy_id.return_value = row, context
    monkeypatch.setattr(details, "PersonnelLookupService", lambda _: lookup)
    monkeypatch.setattr(details, "load_dossier_read_snapshot", lambda *_: {"prepared": True})
    monkeypatch.setattr(details, "require_permission", lambda *_: True)
    monkeypatch.setattr(details, "run_in_background", lambda parent, operation, success, **kw: success(operation()))
    opener = MagicMock()
    monkeypatch.setattr(details, "open_personnel_dossier_dialog", opener)
    widget._open_related()
    lookup.resolve_dossier_for_legacy_id.assert_called_once_with("202")
    assert opener.call_args.args[1] is row
    assert opener.call_args.kwargs["readonly_snapshot"] == {"prepared": True}
    widget.close()


def test_closed_comparison_does_not_open_late_dossier(app, duplicate_alert, monkeypatch):
    widget = details.CareerAlertDetailsDialog(None, duplicate_alert,
        Settings(database_path=Path("unused"), mysql_view_mois="2026-09"), MagicMock(), MagicMock())
    queued = []
    monkeypatch.setattr(details, "require_permission", lambda *_: True)
    monkeypatch.setattr(details, "run_in_background", lambda parent, operation, success, **kw: queued.append(success))
    opener = MagicMock()
    monkeypatch.setattr(details, "open_personnel_dossier_dialog", opener)
    widget._open_related()
    widget.reject()
    queued[0]((MagicMock(), None, {}))
    opener.assert_not_called()


def test_dossier_banner_is_readable_and_opens_explanation(app, duplicate_alert, monkeypatch):
    from test_personnel_dossier_open import _row
    from sme_gend.ui import personnel_dossier_dialog as module
    from sme_gend.repositories.mysql_connection import MySQLConnection
    forbidden = MagicMock(side_effect=AssertionError("Database access in GUI thread"))
    monkeypatch.setattr(MySQLConnection, "connect", forbidden)
    snapshot = {
        "events": {k: [] for k in ("missions", "stages", "malades", "desertions", "conges")},
        "dossier": {k: [] for k in ("list_diplomes", "list_distinctions", "list_passations", "list_affectations")},
        "grades": [], "structures": [], "positions": [], "timeline": [], "situation": None,
        "alerts": [duplicate_alert], "media": {"root": Path("unused"), "photo": None, "grade": None},
    }
    settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    widget = module.PersonnelDossierDialog(None, _row(), MagicMock(), MagicMock(settings=settings),
                                          allow_edit=False, readonly_snapshot=snapshot)
    assert "#fff8e1" in widget._alerts_label.styleSheet()
    assert "SAME NAME [fiche 202]" in widget._alerts_label.text()
    assert "MATRICULE_DUPLIQUE" not in widget._alerts_label.text()
    show = MagicMock()
    monkeypatch.setattr(details, "show_career_alert_details", show)
    widget._on_career_alert_link("alert:0")
    assert show.call_args.args[1] is duplicate_alert
    show.reset_mock()
    widget._on_career_alert_link("alert:-1")
    widget._on_career_alert_link("https://invalid")
    show.assert_not_called()
    forbidden.assert_not_called()
    widget.close()
