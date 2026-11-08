"""Explicit document-based monthly closure; legacy archives keep their own workflow."""
from dataclasses import dataclass, replace
from contextlib import contextmanager
import json

from sme_gend.services.action_permissions import assert_any_permission
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm, shift_sme_month_yyyy_mm

KEY_CYCLE_MODE = "sme_document_cycle"
LEGACY = "legacy"
DOCUMENTS = "publications"


class LegacyCycleDisabledError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentClosureResult:
    source_month: str
    target_month: str
    publication_id: int
    revision_id: str
    projected_agents: int
    created_rows: int
    operation_warning: str = ""


@dataclass(frozen=True)
class DocumentCycleActivationResult:
    mode: str
    active_month: str
    publication_id: int
    operation_warning: str = ""


@contextmanager
def _commit_confirmation(results):
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    try:
        yield
    except CommittedMirrorError:
        if not results:
            raise
        results[0] = replace(results[0], operation_warning="Opération confirmée dans MySQL ; "
            "la copie locale du journal reste à vérifier. Actualisez le pilotage ; ne recommencez pas l’opération.")


def _mode(payload):
    if payload is None:
        return LEGACY
    if not isinstance(payload, dict) or payload.get("mode") not in {LEGACY, DOCUMENTS}:
        raise ValueError("Mode de cycle mensuel invalide. Faites vérifier la configuration partagée.")
    return payload["mode"]


def get_cycle_mode(settings):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    return _mode(AppConfigRepository(settings).get_json(KEY_CYCLE_MODE))


def assert_legacy_closure(settings):
    if get_cycle_mode(settings) == DOCUMENTS:
        raise LegacyCycleDisabledError("Le cycle documentaire est actif. Clôturez depuis le pilotage avec une version SME validée. "
                         "Pour importer une archive historique, désactivez l’option d’archivage et de clôture dans l’assistant.")
    if _has_dated_movements(settings):
        raise LegacyCycleDisabledError(
            "Des mouvements d’effectif datés sont enregistrés. Activez le cycle documentaire depuis le pilotage "
            "pour clôturer et projeter le mois suivant sans recopier les anciennes situations. "
            "Les imports d’archives historiques restent possibles sans l’option d’archivage et de clôture.")


def _has_dated_movements(settings):
    from sme_gend.services.membership_scope import membership_projection_enabled
    if not membership_projection_enabled(settings):
        return False
    from sme_gend.repositories.mysql_connection import MySQLConnection
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1 FROM agent_effectif_movement LIMIT 1')
            return cursor.fetchone() is not None


def assert_legacy_month_fill(settings):
    if get_cycle_mode(settings) == DOCUMENTS or _has_dated_movements(settings):
        raise LegacyCycleDisabledError(
            "La reprise classique ne peut pas recopier les situations de ce cycle. "
            "Activez le cycle documentaire si nécessaire, puis ouvrez le mois suivant depuis le pilotage "
            "avec une version SME validée. Les archives historiques peuvent toujours être importées.")


@contextmanager
def legacy_month_transition(settings):
    """Serialize migrated legacy copies with the first dated movement/activation.

    The caller rechecks its legacy guard inside this barrier before writing.
    Unmigrated databases keep their established transition transaction.
    """
    from sme_gend.services.membership_scope import membership_projection_enabled
    if membership_projection_enabled(settings):
        from sme_gend.services.publication_write_barrier import publication_write_barrier
        with publication_write_barrier(settings, exclusive=True):
            yield
    else:
        yield


def _locked_mode(settings):
    from sme_gend.repositories.mysql_connection import MySQLConnection
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE", (KEY_CYCLE_MODE,))
            row = cursor.fetchone()
    payload = row[0] if row else None
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    return _mode(payload)


def _canonical_month(value):
    month = canonical_sme_mois_yyyy_mm(value or "")
    if not month:
        raise ValueError("Mois invalide : indiquez YYYY-MM.")
    return month


def _validated_current(settings, month, publication_id, expected_version):
    from sme_gend.services.sme_publication_registry import (
        require_validated_publication, assert_publication_current_data,
    )
    publication = require_validated_publication(settings, month, publication_id=publication_id,
                                               expected_version=expected_version, for_update=True)
    assert_publication_current_data(settings, publication, for_update=True)
    return publication


def activate_document_cycle(settings, *, expected_month, publication_id, expected_publication_version, reason,
                            clients_updated_confirmed=False):
    """An administrator explicitly adopts the cycle after validating the active month."""
    from sme_gend.services.auth_guard import session_has_admin_role
    if not session_has_admin_role():
        raise PermissionError("L’activation du cycle documentaire est réservée aux administrateurs.")
    assert_any_permission("workflow.cycle_mode")
    assert_any_permission("publications.view")
    assert_any_permission("publications.verify")
    if clients_updated_confirmed is not True:
        raise ValueError("Avant l’activation, mettez tous les postes à jour vers la version 0.2 ou ultérieure et fermez "
                         "les anciens exécutables. Le fonctionnement simultané avec les versions 0.1.x n’est pas compatible.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Indiquez le motif d’activation du nouveau cycle.")
    from sme_gend.services.active_month_guard import period_state_write, require_month_snapshot
    require_month_snapshot(settings, expected_month)
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.shared_reference_write import mark_shared_data_write
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    results = []
    with _commit_confirmation(results), publication_write_barrier(settings, exclusive=True), period_state_write(
            settings, expected=expected_month, reason="document_cycle_enabled") as change:
        if _locked_mode(settings) != LEGACY:
            raise ValueError("Le cycle documentaire est déjà actif. Actualisez le pilotage.")
        month = change.before.active_month
        if not month:
            raise ValueError("Définissez un mois de travail unique avant d’activer ce cycle.")
        if sum(canonical_sme_mois_yyyy_mm(str(row[1])) == month for row in change.before.rows) != 1:
            raise ValueError("Plusieurs périodes correspondent au mois de travail. Faites les rapprocher avant d’activer le cycle documentaire.")
        publication = _validated_current(settings, month, publication_id, expected_publication_version)
        repository = AppConfigRepository(settings)
        repository.set_json(KEY_CYCLE_MODE, {"mode": DOCUMENTS, "activated_month": month,
            "publication_id": publication.id, "reason": reason, "actor": resolve_audit_actor(),
            "minimum_client_version": "0.2", "clients_updated_confirmed": True})
        repository.append_audit(action="sme_document_cycle_enabled", module="workflow", sme_mois_context=month,
            actor=resolve_audit_actor(), payload={"publication_id": publication.id, "motif": reason,
                                               "minimum_client_version": "0.2", "clients_updated_confirmed": True})
        mark_shared_data_write(True)
        results.append(DocumentCycleActivationResult(DOCUMENTS, month, publication.id))
    return results[0]


def close_document_month(settings, source_month, target_month, *, expected_month,
                         publication_id, expected_publication_version):
    """Validate source evidence and create only next month's projected situation atomically."""
    assert_any_permission("workflow.close_month")
    assert_any_permission("publications.view")
    assert_any_permission("publications.verify")
    source, target = _canonical_month(source_month), _canonical_month(target_month)
    if shift_sme_month_yyyy_mm(source, delta_months=1) != target:
        raise ValueError("Le cycle documentaire ouvre uniquement le mois immédiatement suivant.")
    from sme_gend.services.active_month_guard import period_state_write, require_month_snapshot
    require_month_snapshot(settings, expected_month)
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.mysql_import_period import refresh_est_actuelle_flags
    from sme_gend.services.shared_reference_write import mark_shared_data_write
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    results = []
    with _commit_confirmation(results), publication_write_barrier(settings, exclusive=True), period_state_write(
            settings, expected=expected_month, reason="document_month_closed", mois=source) as change:
        if _locked_mode(settings) != DOCUMENTS:
            raise ValueError("Activez explicitement le cycle documentaire avant de l’utiliser.")
        if change.before.active_month != source:
            raise ValueError("Le mois de travail a changé. Actualisez le pilotage avant de clôturer.")
        publication = _validated_current(settings, source, publication_id, expected_publication_version)
        from sme_gend.services.month_projection import prepare_month_projection
        projection = prepare_month_projection(settings, target, source)
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                source_ids = [int(row[0]) for row in change.before.rows
                              if canonical_sme_mois_yyyy_mm(str(row[1])) == source]
                if not source_ids:
                    raise ValueError("La période source n’existe plus. Aucune clôture effectuée.")
                placeholders = ",".join(["%s"] * len(source_ids))
                cursor.execute(f"UPDATE sme_periode SET statut='Fermé' WHERE id IN ({placeholders})", tuple(source_ids))
                cursor.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=%s", (projection.period_id,))
            refresh_est_actuelle_flags(connection, target)
        repository = AppConfigRepository(settings)
        repository.append_audit(action="sme_document_month_closed", module="workflow", sme_mois_context=source,
            actor=resolve_audit_actor(), payload={"mois_cloture": source, "mois_suivant": target,
                "publication_id": publication.id, "revision_id": publication.revision_id,
                "projected_agents": projection.projected_agents, "created_rows": projection.created_rows})
        mark_shared_data_write(True)
        results.append(DocumentClosureResult(source, target, publication.id, publication.revision_id,
                                              projection.projected_agents, projection.created_rows))
    from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
    invalidate_editing_state_cache()
    return results[0]
