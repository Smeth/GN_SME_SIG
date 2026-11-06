from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.models.agent_evenement import AgentCongeRow, AgentDesertionRow, AgentMissionRow
from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog
from sme_gend.ui.agent_mission_edit_dialog import AgentMissionEditDialog
from sme_gend.ui.conge_edit_dialog import CongeEditDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_mission_dialog_prefills_and_returns_values(qapp):
    dialog = AgentMissionEditDialog(
        None,
        mission=AgentMissionRow(
            "2026-04-01", "2026-04-10", "Mission ONU", "Libreville",
            "REF-4", "2026-04", 13, "Observation",
        ),
    )

    assert dialog.values() == {
        "date_debut": "01/04/2026",
        "date_fin": "10/04/2026",
        "nature": "Mission ONU",
        "lieu": "Libreville",
        "reference_dossier": "REF-4",
        "observation": "Observation",
    }


def test_desertion_dialog_prefills_and_returns_values(qapp):
    dialog = AgentDesertionEditDialog(
        None, desertion=AgentDesertionRow("2026-04-03", "2026-04", 7, "Observation")
    )

    assert dialog.values() == {
        "date_desertion": "03/04/2026",
        "observation": "Observation",
    }


def test_conge_dialog_prefills_and_returns_values(qapp):
    dialog = CongeEditDialog(
        None,
        MagicMock(),
        "agent-1",
        sme_periode_mois="2026-04",
        conge=AgentCongeRow(
            "ADMIN", "Congé administratif", "2026-04-02", "2026-04-08",
            "7.0", "Observation", "2026-04", "saisie", id=5,
        ),
    )

    values = dialog.values()
    assert values["type_code"] == "ADMIN"
    assert values["date_debut"].isoformat() == "2026-04-02"
    assert values["date_fin"].isoformat() == "2026-04-08"
    assert values["quota_jours"] == 7.0
    assert values["observation"] == "Observation"
    assert values["sme_mois"] == "2026-04"


def test_conge_dialog_recalculates_days_when_date_changes(qapp):
    dialog = CongeEditDialog(
        None,
        MagicMock(),
        "agent-1",
        sme_periode_mois="2026-04",
        conge=AgentCongeRow(
            "ADMIN", "Congé administratif", "2026-04-02", "2026-04-08",
            "7.0", None, "2026-04", "saisie", id=5,
        ),
    )

    dialog._fin.set_from_text("09/04/2026")

    assert dialog._jours.text() == "8 jours"