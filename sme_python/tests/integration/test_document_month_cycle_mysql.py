from datetime import date
from types import SimpleNamespace
import json

import pytest

from test_restore_mysql import isolated_mysql
from test_publication_registry_mysql import migrate, revision
from sme_gend.services import document_month_cycle as cycle, sme_publication_registry as registry
from sme_gend.services.sme_workspace_service import SmeWorkspaceService


def adopted(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, archive = migrate(isolated_mysql, tmp_path, monkeypatch)
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: settings)
    publication = registry.register_export(settings, revision(settings, tmp_path / "document"))
    publication = registry.validate_publication(settings, publication.id, expected_version=publication.version)
    workspace = SmeWorkspaceService(settings)
    snapshot = workspace.load_active_month_snapshot()
    result = cycle.activate_document_cycle(settings, expected_month=snapshot, publication_id=publication.id,
        expected_publication_version=publication.version, reason="Adoption explicite pour la recette synthétique",
        clients_updated_confirmed=True)
    assert result.mode == cycle.DOCUMENTS
    return settings, connect, params, workspace, publication


def close(settings, workspace, publication):
    return cycle.close_document_month(settings, "2026-11", "2026-12", expected_month=workspace.load_active_month_snapshot(),
        publication_id=publication.id, expected_publication_version=publication.version)


def test_document_cycle_keeps_source_history_and_projects_only_the_next_situation(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,agent_id,date_debut,date_fin,nature_libelle FROM agent_mission ORDER BY id")
            history = cursor.fetchall()
            cursor.execute("SELECT date_debut,date_fin FROM carriere_situation WHERE id=1")
            original_dates = cursor.fetchone()
    result = close(settings, workspace, publication)
    assert result.source_month == "2026-11" and result.target_month == "2026-12"
    assert result.created_rows == result.projected_agents == 1
    assert workspace.get_active_mois() == "2026-12"
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT annee_mois,statut FROM sme_periode ORDER BY annee_mois")
            assert cursor.fetchall() == (("2026-11", "Fermé"), ("2026-12", "Ouvert"))
            cursor.execute("SELECT id,agent_id,date_debut,date_fin,nature_libelle FROM agent_mission ORDER BY id")
            assert cursor.fetchall() == history
            cursor.execute("SELECT date_debut,date_fin FROM carriere_situation WHERE id=1")
            assert cursor.fetchone() == original_dates
            cursor.execute("SELECT COUNT(*) FROM import_lot")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT payload FROM audit_journal WHERE action='sme_document_month_closed'")
            assert json.loads(cursor.fetchone()[0])["publication_id"] == publication.id
    assert registry.require_validated_publication(settings, "2026-11").id == publication.id


def test_changed_month_cannot_close_with_an_older_validated_document(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE agent SET noms='CORRECTION ULTERIEURE' WHERE id=1")
    with pytest.raises(registry.PublicationDataChanged):
        close(settings, workspace, publication)
    assert workspace.get_active_mois() == "2026-11"
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM sme_periode WHERE annee_mois='2026-12'")
            assert cursor.fetchone()[0] == 0


def test_existing_archived_target_is_never_replaced(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(annee_mois,date_dernier_jour_mois,statut) VALUES('2026-12','2026-12-31','Fermé')")
    with pytest.raises(ValueError):
        close(settings, workspace, publication)
    assert workspace.get_active_mois() == "2026-11"
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT statut FROM sme_periode WHERE annee_mois='2026-12'")
            assert cursor.fetchone()[0] == "Fermé"


def test_old_cycle_cannot_bypass_document_approval_after_explicit_activation(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    from sme_gend.services.sme_month_closure import run_piste_a_month_closure, import_existing_db_for_month
    with pytest.raises(cycle.LegacyCycleDisabledError):
        run_piste_a_month_closure(settings, "2026-11", "2026-12")
    with pytest.raises(cycle.LegacyCycleDisabledError):
        import_existing_db_for_month(settings, tmp_path / "unused.mdb", "2026-11", "2026-12")
    assert workspace.get_active_mois() == "2026-11"


def test_other_month_changes_do_not_invalidate_current_month_evidence(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO sme_periode(annee_mois,date_dernier_jour_mois,statut) VALUES('2026-08','2026-08-31','Fermé')")
            august = cursor.lastrowid
            cursor.execute("INSERT INTO agent(id,legacy_access_id,noms) VALUES(22,'NEW-AUGUST','AUTRE AGENT HISTORIQUE')")
            cursor.execute("INSERT INTO carriere_situation(agent_id,sme_periode_id,position_admin_libelle,date_debut,date_fin) "
                           "VALUES(22,%s,'Présent','2026-08-01','2026-08-31')", (august,))
    result = close(settings, workspace, publication)
    assert result.target_month == "2026-12"
    assert registry.get_publication(settings, publication.id).status == "valide"


@pytest.mark.parametrize("legacy_month", ["202611", "112026"])
def test_source_period_legacy_labels_close_by_canonical_identity(isolated_mysql, tmp_path, monkeypatch, legacy_month):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sme_periode SET annee_mois=%s WHERE id=1", (legacy_month,))
    # Imports may retain their historical month spelling. The same publication
    # must still describe the same observed month without rewriting that label.
    result = close(settings, workspace, publication)
    assert result.source_month == "2026-11"
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT annee_mois,statut FROM sme_periode WHERE id=1")
            assert cursor.fetchone() == (legacy_month, "Fermé")


def test_manual_activation_cannot_bypass_document_cycle(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    with pytest.raises(cycle.LegacyCycleDisabledError):
        workspace.set_active_mois("2026-12", expected=workspace.load_active_month_snapshot())
    assert workspace.get_active_mois() == "2026-11"


def test_delegated_closure_needs_no_permission_for_free_calendar_activation(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, workspace, publication = adopted(isolated_mysql, tmp_path, monkeypatch)
    rights = {"workflow.close_month", "publications.view", "publications.verify"}
    auth = SimpleNamespace(session_has_permission=lambda permission: permission in rights,
        is_enforced=lambda: True, current_session=lambda: SimpleNamespace(role="delegated"))
    monkeypatch.setattr("sme_gend.services.auth_context.get_app_auth", lambda: auth)
    result = close(settings, workspace, publication)
    assert result.target_month == "2026-12"
