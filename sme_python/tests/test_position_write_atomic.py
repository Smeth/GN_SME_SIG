"""No database write before confirmation; position and register commit together."""
from contextlib import contextmanager, nullcontext
from dataclasses import fields
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import padmin_satellite_apply as prompts
from sme_gend.ui.views.edition_sme_view import _EditionModel, _COL_IDX


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def synthetic_row():
    values = {item.name: None for item in fields(ListeGeneraleRow)}
    values.update(id="1", noms="SYNTHETIC", access_join_id="42", position_admin="Présent")
    return ListeGeneraleRow(**values)


@pytest.fixture
def editable_model(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("sme_gend.services.temporal_events.position_edit",lambda *a,**kw:nullcontext())
    model = _EditionModel()
    row = synthetic_row()
    settings = Settings(database_path=tmp_path / "unused.mdb", mysql_view_mois="2026-09")
    model.load([row], None, [], [], [], [], settings=settings)
    state = {"position": "Présent", "register": None, "active": False}
    events = []
    @contextmanager
    def transaction(*args, **kwargs):
        previous = dict(state)
        state["active"] = True
        events.append("begin")
        try:
            yield
        except Exception:
            state.clear()
            state.update(previous)
            events.append("rollback")
            raise
        else:
            state["active"] = False
            events.append("commit")
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write", transaction)
    def persist(before, after):
        state["position"] = after.position_admin
        events.append("position")
    model._persist_update = persist
    model._after_db_mutation = lambda: None
    return model, state, events


def test_cancelled_position_dialog_never_writes_or_creates_undo(editable_model):
    model, state, events = editable_model
    def cancel(*args):
        assert not state["active"]
        events.append("dialog")
        return False
    model.position_change_prompt = cancel
    assert not model.setData(model.index(0, _COL_IDX["position_admin"]), "CongéAdmin")
    assert events == ["dialog"]
    assert state["position"] == "Présent"
    assert model._rows[0].position_admin == "Présent" and model.active_undo_count() == 0


@pytest.mark.parametrize("fail", [False, True])
def test_position_and_register_share_transaction_after_dialog(editable_model, fail):
    model, state, events = editable_model
    errors, saved = [], []
    model.save_err.connect(errors.append)
    model.save_ok.connect(saved.append)
    def dialog(*args):
        assert not state["active"]
        events.append("dialog")
        def write_register():
            assert state["active"]
            state["register"] = "synthetic leave"
            events.append("register")
            if fail:
                raise RuntimeError("synthetic register failure")
        return write_register
    model.position_change_prompt = dialog
    assert model.setData(model.index(0, _COL_IDX["position_admin"]), "CongéAdmin") == (not fail)
    assert events == ["dialog", "begin", "position", "register", "rollback" if fail else "commit"]
    assert state["position"] == ("Présent" if fail else "CongéAdmin")
    assert state["register"] == (None if fail else "synthetic leave")
    assert model.active_undo_count() == (0 if fail else 1)
    assert len(errors) == int(fail) and len(saved) == int(not fail)


@pytest.mark.parametrize("kind", ["ADMIN", "EXPECTATIVE", "Détaché", "Inst Mise Route", "Punis", "Prison MA", "Déserteur"])
def test_prepared_additional_form_has_no_write_until_called(monkeypatch, kind):
    dialog = MagicMock()
    dialog.exec.return_value = QDialog.DialogCode.Accepted
    dialog.values.return_value = {"date_debut": date(2026, 9, 1)}
    for name in ("conge_edit_dialog.CongeEditDialog", "agent_desertion_edit_dialog.AgentDesertionEditDialog", "position_admin_event_dialog.PositionAdminEventDialog"):
        monkeypatch.setattr("sme_gend.ui." + name, lambda *a, **kw: dialog)
    events = MagicMock()
    monkeypatch.setattr("sme_gend.services.agent_dossier_events_service.AgentDossierEventsService", lambda s: events)
    save_position = MagicMock()
    monkeypatch.setattr("sme_gend.services.position_admin_events.add_position_event", save_position)
    monkeypatch.setattr("sme_gend.config.liste_generale_scope.build_export_mois_context", lambda s: SimpleNamespace(export_mois="2026-09"))
    action = prompts.prompt_additional_position_for_row(None, object(), synthetic_row(), kind=kind, prepare_only=True)
    events.add_conge.assert_not_called()
    events.add_desertion.assert_not_called()
    save_position.assert_not_called()
    assert callable(action)
    action()
    target = events.add_conge if kind in ("ADMIN", "EXPECTATIVE") else events.add_desertion if kind == "Déserteur" else save_position
    target.assert_called_once()


@pytest.mark.parametrize("old,new,kind", [("Présent", "Malade", "malade"), ("Présent", "Mission", "mission"),
    ("Présent", "StageLocal", "stage_local"), ("Présent", "StageEx", "stage_ex"),
    ("Présent", "Stage interne", "stage_interne")])
def test_prepared_satellite_form_has_no_write_until_called(monkeypatch, old, new, kind):
    monkeypatch.setattr(prompts, "NumeriqueSatelliteRepository", lambda s: object())
    monkeypatch.setattr(prompts, "_load_existing", lambda *a: None)
    monkeypatch.setattr(prompts.PadminSatelliteDialog, "run", lambda *a, **kw: object())
    writer = MagicMock()
    monkeypatch.setattr(prompts, "_apply_form", writer)
    action = prompts.prompt_satellite_for_row(None, object(), synthetic_row(), old_padmin=old, new_padmin=new, prepare_only=True)
    writer.assert_not_called()
    assert callable(action)
    action()
    writer.assert_called_once()


@pytest.mark.parametrize("family", ["diplome", "passation", "affectation", "distinction"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_history_mutation_rejects_event_outside_displayed_agent(monkeypatch, family, operation):
    monkeypatch.setattr("sme_gend.repositories.write_guard.lock_agent", lambda *a, **kw: 7)
    from sme_gend.services.agent_dossier_service import AgentDossierService
    service = AgentDossierService.__new__(AgentDossierService)
    service._settings = object()
    service._repo = MagicMock()
    service._repo.resolve_agent_id.return_value = 7
    getattr(service._repo, "list_" + family + "s").return_value = []
    service._project_carriere = MagicMock()
    service._log_history = MagicMock()
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write", lambda *a, **kw: nullcontext())
    monkeypatch.setattr("sme_gend.services.sensitive_data_policy.assert_may_mutate_dossier_history", lambda **kw: None)
    kwargs = {"structure_code": "UNIT TEST", "date_affectation": date(2026, 9, 1)} if family == "affectation" and operation == "update" else {}
    with pytest.raises(ValueError, match="agent|dossier"):
        getattr(service, operation + "_" + family)(synthetic_row(), 999, **kwargs)
    getattr(service._repo, operation + "_" + family).assert_not_called()


@pytest.mark.parametrize("operation", ["add", "update", "delete"])
def test_malade_service_checks_permission_before_mutating(monkeypatch, operation):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    service = AgentDossierEventsService.__new__(AgentDossierEventsService)
    service._settings = object()
    service._repo = MagicMock()
    service._assert_history_mutation = MagicMock(side_effect=PermissionError("synthetic denied"))
    if operation == 'add':
        monkeypatch.setattr('sme_gend.services.sensitive_data_policy.assert_may_add_dated_event',
                            MagicMock(side_effect=PermissionError('synthetic denied')))
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write", lambda *a, **kw: nullcontext())
    kwargs = {"date_debut": date(2026, 9, 1), "date_fin": None, "pathologie": "SYNTHETIC", "observation": None} if operation != "delete" else {}
    with pytest.raises(PermissionError):
        getattr(service, operation + "_malade")("42" if operation == "add" else 9, **kwargs)
    assert not service._repo.mock_calls


@pytest.mark.parametrize("operation", ["field", "replacement"])
def test_grouped_position_changes_also_honor_cancelled_dialog(editable_model, operation):
    model, state, events = editable_model
    model.position_change_prompt = lambda *args: False
    if operation == "field":
        count, unchanged, errors = model.bulk_set_field([0], "position_admin", "CongéAdmin")
    else:
        count, errors = model.bulk_apply_replacements([(0, "position_admin", "CongéAdmin")])
    assert count == 0 and errors
    assert events == [] and state["position"] == "Présent"
    assert model.active_undo_count() == 0


def test_shared_satellite_preparation_defers_writes(monkeypatch):
    monkeypatch.setattr(prompts, "_load_bulk_snapshots", lambda sat,kinds:{kind:{} for kind in kinds})
    monkeypatch.setattr(prompts, "NumeriqueSatelliteRepository", lambda s: object())
    monkeypatch.setattr(prompts.PadminSatelliteDialog, "run", lambda *a, **kw: object())
    writer = MagicMock()
    monkeypatch.setattr(prompts, "_apply_form", writer)
    row = synthetic_row()
    action = prompts.prompt_satellite_bulk_shared(None, object(), [row], new_padmin="Mission", prepare_only=True)
    writer.assert_not_called()
    assert callable(action)
    action(row)
    writer.assert_called_once()


def test_punis_selection_cancels_shared_form_before_any_write(monkeypatch):
    from sme_gend.ui.views.malade_punis_view import MaladePunisView
    row = synthetic_row()
    personnel = MagicMock()
    monkeypatch.setattr("sme_gend.services.personnel_service.PersonnelService", lambda s: personnel)
    monkeypatch.setattr(prompts, "prompt_satellite_bulk_shared", lambda *a, **kw: False)
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.invalidate_liste_cache", lambda s: None)
    monkeypatch.setattr("sme_gend.ui.views.malade_punis_view.QMessageBox.information", MagicMock())
    view = SimpleNamespace(_check_can_edit=lambda **kw: True, _punis_target_ids=lambda: [row.id],
        _combo_new_padmin=SimpleNamespace(currentText=lambda: "Mission"), _scoped_settings=object(),
        _liste=SimpleNamespace(fetch_all=lambda: [row]), _punis_edit_motif=lambda: "SYNTHETIC",
        _punis_checked={row.id}, _reload_punis=MagicMock())
    MaladePunisView._apply_padmin_selection(view, skip_confirm=True)
    personnel.update_personnel.assert_not_called()
    assert view._punis_checked == {row.id}
