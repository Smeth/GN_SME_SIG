from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.services.padmin_satellite import (
    kind_requires_satellite_prompt,
    padmin_label_for_kind,
    satellite_kind,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_agent_stage_dialog_add_and_edit_values(qapp) -> None:
    from sme_gend.models.agent_evenement import AgentStageRow
    from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog

    new_dialog = AgentStageEditDialog(None)
    new_dialog._type.setCurrentIndex(new_dialog._type.findData("INTERNE"))
    new_dialog._nature.setText("Formation interne")
    new_dialog._lieu.setText("Brazzaville")
    new_dialog._date_debut.set_from_text("2026-09-01")
    new_dialog._date_fin.set_from_text("2026-09-30")
    new_dialog._observation.setPlainText("Session annuelle")
    assert new_dialog.values() == {
        "type_code": "INTERNE",
        "nature": "Formation interne",
        "lieu": "Brazzaville",
        "date_debut": "01/09/2026",
        "date_fin": "30/09/2026",
        "observation": "Session annuelle",
        "reference_dossier": None,
        "session": None,
    }

    existing = AgentStageRow(
        "INTERNE", "2026-09-01", "2026-09-30", "Formation interne",
        "Brazzaville", "Session annuelle", "2026-09", id=7,
    )
    edit_dialog = AgentStageEditDialog(None, stage=existing)
    assert edit_dialog.values() == new_dialog.values()


def test_stage_interne_position_variants_trigger_stage_form(qapp) -> None:
    for value in ("Stage interne", "StageInterne", " STAGE   INTERNE "):
        assert satellite_kind(value) == "stage_interne"
        assert kind_requires_satellite_prompt("Présent", value) == "stage_interne"
    assert padmin_label_for_kind("stage_interne") == "Stage interne"


def test_stage_internal_satellite_uses_internal_type_code(monkeypatch) -> None:
    from sme_gend.services.padmin_satellite_apply import _apply_form
    from sme_gend.ui.padmin_satellite_dialog import SatelliteFormResult
    from sme_gend.models.numerique_satellite import StageDetail

    calls = []

    class Repository:
        def upsert_stage(self, person_id, detail, **kwargs):
            calls.append((person_id, detail, kwargs))

    form = SatelliteFormResult(
        kind="stage_interne",
        stage=StageDetail("Formation interne", "Brazzaville", "01/09/2026"),
        stage_date_debut="01/09/2026",
        stage_date_fin=None,
    )

    _apply_form(Repository(), "agent-1", form)

    assert calls[0][2]["type_code"] == "INTERNE"
