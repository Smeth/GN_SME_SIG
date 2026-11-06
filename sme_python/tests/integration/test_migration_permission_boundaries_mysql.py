"""Rights and exceptional reopening checked against an isolated native database."""
import json

import pytest

from test_restore_mysql import isolated_mysql, _lot11_closure
from sme_gend.services import auth_guard, sme_month_closure, sme_editing_policy
from sme_gend.services.sme_workspace_service import SmeWorkspaceService


def _state(connect, params):
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            result = []
            for query in ("SELECT id,annee_mois,statut FROM sme_periode ORDER BY id",
                          "SELECT id,est_actuelle,date_debut,date_fin FROM carriere_situation ORDER BY id",
                          "SELECT config_key,payload FROM app_config ORDER BY config_key",
                          "SELECT action,payload FROM audit_journal ORDER BY id"):
                cursor.execute(query)
                result.append(cursor.fetchall())
            return result


@pytest.mark.parametrize("operation", ["activate", "reopen", "close", "import"])
def test_denied_workflow_cannot_mutate_native_periods_or_audit(isolated_mysql, monkeypatch, tmp_path, operation):
    settings, connect, params, closure = _lot11_closure(isolated_mysql, monkeypatch, tmp_path)
    service = SmeWorkspaceService(settings)
    expected = service.load_active_month_snapshot()
    before = _state(connect, params)
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: False)
    operations = {
        "activate": lambda: service.set_active_mois("2026-10", expected=expected),
        "reopen": lambda: sme_editing_policy.reopen_closed_month_for_corrections(settings, "2026-10", "synthetic", "reason"),
        "close": lambda: closure.run_piste_a_month_closure(settings, "2026-09", "2026-10", expected=expected),
        "import": lambda: closure.import_existing_db_for_month(settings, tmp_path / "unused.mdb", "2026-09"),
    }
    with pytest.raises(PermissionError):
        operations[operation]()
    assert _state(connect, params) == before


def test_closed_activation_requires_reopening_right_and_reason_then_new_snapshot(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params, closure = _lot11_closure(isolated_mysql, monkeypatch, tmp_path)
    service = SmeWorkspaceService(settings)
    original = service.load_active_month_snapshot()
    permissions = {"workflow.set_active_month"}
    monkeypatch.setattr(auth_guard, "session_has_permission", lambda permission: permission in permissions)
    with pytest.raises(ValueError, match="réouverture exceptionnelle"):
        service.set_active_mois("2026-10", expected=original)
    with pytest.raises(PermissionError):
        sme_editing_policy.reopen_closed_month_for_corrections(settings, "2026-10", "synthetic", "valid reason")
    permissions.add("workflow.reopen_month")
    result = sme_editing_policy.reopen_closed_month_for_corrections(settings, "2026-10", "synthetic", "")
    assert result.rows_updated == 0
    assert service.load_active_month_snapshot() == original
    reason = "Correction historique autorisée pour le test"
    result = sme_editing_policy.reopen_closed_month_for_corrections(settings, "2026-10", "synthetic", reason)
    assert result.rows_updated == 1
    assert service.get_active_mois() == "2026-09"
    with pytest.raises(ValueError, match="changé"):
        service.set_active_mois("2026-10", expected=original)
    service.set_active_mois("2026-10", expected=service.load_active_month_snapshot())
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois")
            assert cursor.fetchall() == (("2026-08", "Réouvert"), ("2026-09", "Fermé"), ("2026-10", "Ouvert"))
            cursor.execute("SELECT payload FROM audit_journal WHERE action='sme_reopen_exception'")
            assert any(json.loads(row[0])["motif"] == reason for row in cursor.fetchall())
