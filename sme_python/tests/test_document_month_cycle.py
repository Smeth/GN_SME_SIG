from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, MagicMock
import sys

import pytest

from sme_gend.config.settings import Settings
from sme_gend.config.auth_permissions import admin_role_permissions, lecteur_role_permissions, operateur_role_permissions
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.services.active_month_guard import ActiveMonthSnapshot
from sme_gend.services import document_month_cycle as cycle


def test_publication_permissions_separate_read_create_and_administrative_validation():
    reader, operator, admin = map(set, (lecteur_role_permissions(), operateur_role_permissions(), admin_role_permissions()))
    assert {"publications.view", "publications.verify"} <= reader
    assert not {"publications.create", "publications.download", "publications.validate", "publications.replace", "workflow.cycle_mode"} & reader
    assert {"publications.view", "publications.create", "publications.verify", "publications.download"} <= operator
    assert not {"publications.validate", "publications.replace", "workflow.cycle_mode"} & operator
    assert {"publications.validate", "publications.replace", "workflow.cycle_mode"} <= admin


@pytest.mark.parametrize("operation", ["activate", "close"])
def test_cycle_denies_before_database_or_snapshot_access(monkeypatch, operation):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission", lambda permission: False)
    with pytest.raises(PermissionError):
        if operation == "activate":
            cycle.activate_document_cycle(None, expected_month=None, publication_id=1, expected_publication_version=1, reason="reason")
        else:
            cycle.close_document_month(None, "2026-09", "2026-10", expected_month=None, publication_id=1, expected_publication_version=1)


@pytest.mark.parametrize("payload,expected", [(None, "legacy"), ({"mode": "legacy"}, "legacy"), ({"mode": "publications"}, "publications")])
def test_existing_bases_stay_on_explicit_legacy_mode(payload, expected):
    assert cycle._mode(payload) == expected


@pytest.mark.parametrize("payload", [{}, {"mode": "unknown"}, {"mode": None}, [], "publications"])
def test_corrupt_cycle_configuration_is_not_silently_downgraded(payload):
    with pytest.raises(ValueError):
        cycle._mode(payload)


@pytest.fixture
def workflow(monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission", lambda permission: True)
    settings = Settings(database_path=Path("unused.mdb"), mysql_view_mois="2026-09")
    snapshot = ActiveMonthSnapshot(mysql_database_key(settings), ((1,"2026-09","Ouvert","SME-202609"),), "revision")
    state = {"mode": cycle.DOCUMENTS, "trace": []}
    publication = SimpleNamespace(id=11, version=3, revision_id="synthetic-revision", mois="2026-09", status="valide")
    registry = ModuleType("sme_gend.services.sme_publication_registry")
    registry.require_validated_publication = Mock(side_effect=lambda *a, **kw: state["trace"].append("validated") or publication)
    registry.assert_publication_current_data = Mock(side_effect=lambda *a, **kw: state["trace"].append("fingerprint"))
    monkeypatch.setitem(sys.modules, registry.__name__, registry)
    projection = ModuleType("sme_gend.services.month_projection")
    projection.refresh_month_projection = Mock(side_effect=lambda *a, **kw: state["trace"].append("refresh"))
    projection.prepare_month_projection = Mock(side_effect=lambda *a, **kw: state["trace"].append("project") or
        SimpleNamespace(period_id=2, projected_agents=12, created_rows=12))
    monkeypatch.setitem(sys.modules, projection.__name__, projection)
    @contextmanager
    def barrier(*args, **kwargs):
        assert kwargs == {"exclusive": True}
        state["trace"].append("barrier")
        yield
    @contextmanager
    def period(*args, **kwargs):
        assert state["trace"][0] == "barrier"
        assert kwargs["expected"] is snapshot
        state["trace"].append("period")
        yield SimpleNamespace(before=snapshot)
    monkeypatch.setattr("sme_gend.services.publication_write_barrier.publication_write_barrier", barrier)
    monkeypatch.setattr("sme_gend.services.active_month_guard.period_state_write", period)
    monkeypatch.setattr(cycle, "_locked_mode", lambda settings: state["mode"])
    cursor, connection = MagicMock(), MagicMock()
    connection.cursor.return_value.__enter__.return_value = cursor
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.MySQLConnection",
        lambda settings: SimpleNamespace(connect=lambda **kw: nullcontext(connection)))
    repository = Mock()
    monkeypatch.setattr("sme_gend.repositories.app_config_repository.AppConfigRepository", lambda settings: repository)
    monkeypatch.setattr("sme_gend.services.mysql_import_period.refresh_est_actuelle_flags", Mock())
    monkeypatch.setattr("sme_gend.services.shared_reference_write.mark_shared_data_write", Mock())
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_audit_actor", lambda: "synthetic-admin")
    return SimpleNamespace(settings=settings, snapshot=snapshot, state=state, registry=registry,
        projection=projection, cursor=cursor, repository=repository)


def close(workflow, target="2026-10"):
    return cycle.close_document_month(workflow.settings, "2026-09", target, expected_month=workflow.snapshot,
                                     publication_id=11, expected_publication_version=3)


def test_document_closure_freezes_source_evidence_before_projecting_only_next_month(workflow):
    result = close(workflow)
    assert workflow.state["trace"] == ["barrier", "period", "validated", "fingerprint", "project"]
    workflow.registry.require_validated_publication.assert_called_once_with(workflow.settings, "2026-09",
        publication_id=11, expected_version=3, for_update=True)
    assert result.source_month == "2026-09" and result.target_month == "2026-10" and result.projected_agents == 12
    statements = [call.args[0] for call in workflow.cursor.execute.call_args_list]
    assert len(statements) == 2 and all(statement.startswith("UPDATE sme_periode") for statement in statements)
    workflow.repository.append_audit.assert_called_once()


def test_mutated_source_refuses_closure_without_creating_or_closing_periods(workflow):
    workflow.registry.assert_publication_current_data.side_effect = ValueError("Les données du mois ont changé")
    with pytest.raises(ValueError, match="ont changé"):
        close(workflow)
    workflow.projection.prepare_month_projection.assert_not_called()
    workflow.cursor.execute.assert_not_called()
    workflow.repository.append_audit.assert_not_called()


def test_filled_target_is_never_overwritten(workflow):
    workflow.projection.prepare_month_projection.side_effect = ValueError("Période cible déjà remplie")
    with pytest.raises(ValueError, match="déjà remplie"):
        close(workflow)
    workflow.cursor.execute.assert_not_called()
    workflow.repository.append_audit.assert_not_called()


@pytest.mark.parametrize("target", ["2026-09", "2026-11", "2027-01", "2026-99"])
def test_document_cycle_cannot_skip_or_reinterpret_a_historical_month(workflow, target):
    with pytest.raises(ValueError):
        close(workflow, target)
    assert workflow.state["trace"] == []


def test_cycle_requires_explicit_adoption(workflow):
    workflow.state["mode"] = cycle.LEGACY
    with pytest.raises(ValueError, match="Activez explicitement"):
        close(workflow)
    workflow.registry.require_validated_publication.assert_not_called()


def test_adoption_records_reason_and_current_validated_version(workflow):
    workflow.state["mode"] = cycle.LEGACY
    assert cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
        publication_id=11, expected_publication_version=3, reason=" Adoption approuvée ", clients_updated_confirmed=True).mode == cycle.DOCUMENTS
    payload = workflow.repository.set_json.call_args.args[1]
    assert payload["mode"] == cycle.DOCUMENTS and payload["reason"] == "Adoption approuvée"
    assert payload["publication_id"] == 11
    assert workflow.state["trace"] == ["barrier", "period", "validated", "fingerprint"]


def test_empty_adoption_reason_fails_before_gate(workflow):
    with pytest.raises(ValueError, match="motif"):
        cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
            publication_id=11, expected_publication_version=3, reason=" ", clients_updated_confirmed=True)
    assert workflow.state["trace"] == []


def test_adoption_still_requires_admin_role_even_with_a_forged_permission(workflow, monkeypatch):
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_admin_role", lambda: False)
    with pytest.raises(PermissionError, match="administrateurs"):
        cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
            publication_id=11, expected_publication_version=3, reason="Adoption")
    assert workflow.state["trace"] == []


def test_legacy_closure_refuses_document_mode_before_backup(monkeypatch):
    monkeypatch.setattr(cycle, "get_cycle_mode", lambda settings: cycle.DOCUMENTS)
    monkeypatch.setattr("sme_gend.services.auth_guard.session_has_permission", lambda permission: True)
    from sme_gend.services.sme_month_closure import run_piste_a_month_closure
    with pytest.raises(cycle.LegacyCycleDisabledError, match="cycle documentaire"):
        run_piste_a_month_closure(None, "2026-09", "2026-10")


@pytest.mark.parametrize("activation", [False, True])
def test_confirmed_commit_with_mirror_failure_is_reported_without_inviting_retry(workflow, monkeypatch, activation):
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    @contextmanager
    def committed(*args, **kwargs):
        workflow.state["trace"].append("barrier")
        yield
        raise CommittedMirrorError("synthetic confirmed commit")
    monkeypatch.setattr("sme_gend.services.publication_write_barrier.publication_write_barrier", committed)
    if activation:
        workflow.state["mode"] = cycle.LEGACY
        result = cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
            publication_id=11, expected_publication_version=3, reason="Adoption", clients_updated_confirmed=True)
        assert result.mode == cycle.DOCUMENTS
    else:
        result = close(workflow)
        assert result.target_month == "2026-10"
    assert "confirmée dans MySQL" in result.operation_warning
    workflow.repository.append_audit.assert_called_once()


@pytest.mark.parametrize("confirmation", [False, None, "oui", 1])
def test_adoption_requires_explicit_updated_client_acknowledgement_before_sql(workflow, confirmation):
    with pytest.raises(ValueError, match="tous les postes à jour"):
        cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
            publication_id=11, expected_publication_version=3, reason="Adoption",
            clients_updated_confirmed=confirmation)
    assert workflow.state["trace"] == []
    workflow.cursor.execute.assert_not_called()


def test_adoption_refuses_ambiguous_legacy_periods_before_switching_modes(workflow, monkeypatch):
    workflow.state["mode"] = cycle.LEGACY
    duplicate = replace(workflow.snapshot, rows=workflow.snapshot.rows + ((2, "092026", "Fermé", "LEGACY-2"),))
    @contextmanager
    def ambiguous(*args, **kwargs):
        yield SimpleNamespace(before=duplicate)
    monkeypatch.setattr("sme_gend.services.active_month_guard.period_state_write", ambiguous)
    with pytest.raises(ValueError, match="Plusieurs périodes"):
        cycle.activate_document_cycle(workflow.settings, expected_month=workflow.snapshot,
            publication_id=11, expected_publication_version=3, reason="Adoption", clients_updated_confirmed=True)
    workflow.registry.require_validated_publication.assert_not_called()
    workflow.repository.set_json.assert_not_called()
