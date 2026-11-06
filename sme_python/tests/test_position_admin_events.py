"""Fiches complémentaires des positions administratives et parcours dossier."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from sme_gend.services import padmin_satellite_apply as prompts
from sme_gend.ui.position_admin_event_dialog import PositionAdminEventDialog
from sme_gend.ui.views.edition_sme_view import EditionSmeView


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("position,kind", [
    ("CongéExp", "EXPECTATIVE"), ("Détaché", "Détaché"),
    ("Déserteur", "Déserteur"), ("Inst Mise Route", "Inst Mise Route"),
    ("Punis", "Punis"), ("Prison MA", "Prison MA"),
])
@pytest.mark.parametrize("saved", [True, False])
def test_position_prompt_and_rollback(monkeypatch, position, kind, saved):
    prompt = MagicMock(return_value=saved)
    monkeypatch.setattr(prompts, "prompt_additional_position_for_row", prompt)
    view = SimpleNamespace(_settings=object(), _model=MagicMock(), _lbl_status=MagicMock())
    row = object()
    EditionSmeView._on_padmin_changed(view, row, "Présent", position)
    prompt.assert_called_once_with(view, view._settings, row, kind=kind)
    assert view._model.undo_last.called == (not saved)
    prompt.reset_mock()
    EditionSmeView._on_padmin_changed(view, row, position, position)
    prompt.assert_not_called()


@pytest.mark.parametrize("position", ["Malade", "Mission", "StageLocal", "StageEx", "Stage interne"])
def test_existing_position_cancel_restores_previous_position(monkeypatch, position):
    monkeypatch.setattr(prompts, "prompt_satellite_for_row", lambda *a, **kw: False)
    view = SimpleNamespace(_settings=object(), _model=MagicMock(), _lbl_status=MagicMock())
    EditionSmeView._on_padmin_changed(view, object(), "Présent", position)
    view._model.undo_last.assert_called_once()


@pytest.mark.parametrize("kind", ["Détaché", "Inst Mise Route", "Punis", "Prison MA", "Déserteur"])
@pytest.mark.parametrize("result", ["saved", "cancelled", "error"])
def test_event_register_write(monkeypatch, kind, result):
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Rejected if result == "cancelled" else QDialog.DialogCode.Accepted
    payload = {"date_desertion": "29/10/2026", "observation": "Test"} if kind == "Déserteur" else dict(
        date_debut=date(2026, 10, 29), date_fin=None, lieu="Lieu",
        reference_decision="N°42", observation="Test",
    )
    dialog.values.return_value = payload
    factory = MagicMock(return_value=dialog)
    monkeypatch.setattr("sme_gend.ui.position_admin_event_dialog.PositionAdminEventDialog", factory)
    monkeypatch.setattr("sme_gend.ui.agent_desertion_edit_dialog.AgentDesertionEditDialog", factory)
    events = MagicMock()
    monkeypatch.setattr("sme_gend.services.agent_dossier_events_service.AgentDossierEventsService", lambda s: events)
    save = MagicMock()
    monkeypatch.setattr("sme_gend.services.position_admin_events.add_position_event", save)
    monkeypatch.setattr("sme_gend.config.liste_generale_scope.build_export_mois_context", lambda s: SimpleNamespace(export_mois="2026-10"))
    warning = MagicMock()
    monkeypatch.setattr(prompts.QMessageBox, "warning", warning)
    target = events.add_desertion if kind == "Déserteur" else save
    if result == "error":
        target.side_effect = RuntimeError("Échec base")
    row = SimpleNamespace(noms="Agent test", satellite_join_key=lambda: "42")
    settings = object()
    assert prompts.prompt_additional_position_for_row(None, settings, row, kind=kind) == (result == "saved")
    if result == "cancelled":
        target.assert_not_called()
    elif kind == "Déserteur":
        target.assert_called_once_with("42", **payload)
    else:
        target.assert_called_once_with(settings, "42", position_admin=kind, mois_sme="2026-10", **payload)
    assert warning.called == (result == "error")


def test_conge_expectative_uses_correct_register_type(monkeypatch):
    prompt = MagicMock(return_value=True)
    monkeypatch.setattr(prompts, "prompt_admin_conge_for_row", prompt)
    row, settings = object(), object()
    assert prompts.prompt_additional_position_for_row(None, settings, row, kind="EXPECTATIVE")
    prompt.assert_called_once_with(None, settings, row, type_code="EXPECTATIVE")


@pytest.mark.parametrize("position", ["Détaché", "Inst Mise Route", "Punis", "Prison MA"])
def test_dialog_values_and_date_validation(qapp, monkeypatch, position):
    dialog = PositionAdminEventDialog(None, position_admin=position, agent_label="Agent")
    warning = MagicMock()
    monkeypatch.setattr("sme_gend.ui.position_admin_event_dialog.QMessageBox.warning", warning)
    dialog._validate()
    assert warning.called
    dialog._debut.set_from_text("2026-10-29")
    dialog._fin.set_from_text("2026-10-28")
    dialog._validate()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog._fin.set_from_text("")
    dialog._reference.setText("Décision 42")
    dialog._validate()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.values()["date_fin"] is None
    assert dialog.values()["reference_decision"] == "Décision 42"


def test_event_persistence_links_agent_and_keeps_all_details(monkeypatch):
    from sme_gend.services.position_admin_events import add_position_event, list_position_events
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (123,)
    stored = ("Punis", date(2026, 10, 29), None, "Unité", "DEC-1", "Motif", "2026-10")
    cursor.fetchall.return_value = [stored]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    repository = MagicMock()
    repository._connect.return_value = connection
    monkeypatch.setattr("sme_gend.repositories.mysql_common.BaseMySQLRepository", lambda settings: repository)
    monkeypatch.setattr("sme_gend.services.sensitive_data_policy.assert_may_mutate_dossier_history", lambda **kw: None)
    monkeypatch.setattr('sme_gend.services.temporal_events.event_period', lambda *a: 1)
    monkeypatch.setattr('sme_gend.services.temporal_events.record_position', lambda *a, **kw: None)
    from contextlib import nullcontext
    monkeypatch.setattr('sme_gend.services.personnel_service._personnel_write', lambda *a, **kw: nullcontext())
    add_position_event(object(), "42", position_admin=stored[0], date_debut=stored[1],
                       date_fin=None, lieu=stored[3], reference_decision=stored[4],
                       observation=stored[5], mois_sme=stored[6])
    sql, params = cursor.execute.call_args.args
    assert "INSERT INTO agent_position_event" in sql
    assert params == (123, *stored)
    assert list_position_events(object(), "42") == [stored]
    assert cursor.execute.call_args.args[1] == ("42",)
