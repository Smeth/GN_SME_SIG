"""Congé administratif : fiche dossier, annulation et erreurs de sauvegarde."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QDialog

from sme_gend.services.padmin_satellite_apply import prompt_admin_conge_for_row
from sme_gend.ui.views.edition_sme_view import EditionSmeView


@pytest.mark.parametrize("accepted,save_error", [(True, False), (False, False), (True, True)])
def test_conge_prompt_saves_dossier_or_reports_failure(monkeypatch, accepted, save_error):
    dialog = MagicMock()
    dialog.exec.return_value = (
        QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    )
    payload = dict(type_code="ADMIN", date_debut=date(2026, 10, 29),
                   date_fin=date(2026, 11, 2), quota_jours=5,
                   observation="Autorisation", sme_mois="2026-10")
    dialog.values.return_value = payload
    factory = MagicMock(return_value=dialog)
    events = MagicMock()
    if save_error:
        events.add_conge.side_effect = RuntimeError("Écriture refusée")
    warning = MagicMock()
    monkeypatch.setattr("sme_gend.ui.conge_edit_dialog.CongeEditDialog", factory)
    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_events_service.AgentDossierEventsService",
        MagicMock(return_value=events),
    )
    monkeypatch.setattr(
        "sme_gend.config.liste_generale_scope.build_export_mois_context",
        lambda settings: SimpleNamespace(export_mois="2026-10"),
    )
    monkeypatch.setattr("sme_gend.services.padmin_satellite_apply.QMessageBox.warning", warning)
    row = SimpleNamespace(noms="Agent", satellite_join_key=lambda: "42")
    settings = object()
    assert prompt_admin_conge_for_row(None, settings, row) == (accepted and not save_error)
    factory.assert_called_once_with(None, settings, "42", sme_periode_mois="2026-10")
    if accepted:
        events.add_conge.assert_called_once_with("42", **payload)
    else:
        events.add_conge.assert_not_called()
    assert warning.called == save_error


@pytest.mark.parametrize("saved", [True, False])
def test_position_change_restored_when_conge_not_saved(monkeypatch, saved):
    prompt = MagicMock(return_value=saved)
    monkeypatch.setattr(
        "sme_gend.services.padmin_satellite_apply.prompt_admin_conge_for_row", prompt,
    )
    view = SimpleNamespace(_settings=object(), _model=MagicMock(), _lbl_status=MagicMock())
    row = object()
    EditionSmeView._on_padmin_changed(view, row, "Présent", "CongéAdmin")
    prompt.assert_called_once_with(view, view._settings, row)
    assert view._model.undo_last.called == (not saved)
