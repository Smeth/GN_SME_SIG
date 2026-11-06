"""Alertes opérationnelles pour le tableau de bord SME."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.app_health_service import check_mysql_connectivity, find_latest_backup_zip
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm, find_archive_for_month
from sme_gend.services.sme_workspace_service import SmeWorkspaceService
from sme_gend.services.workflow_task_service import WorkflowTaskService


@dataclass(frozen=True, slots=True)
class WorkflowAlert:
    alert_id: str
    level: str  # info | warn | error
    title: str
    detail: str


def build_workflow_alerts(settings: Settings) -> tuple[WorkflowAlert, ...]:
    alerts: list[WorkflowAlert] = []
    ctx = load_sme_context()
    mois = (ctx.mois_actif or "").strip()
    ws = SmeWorkspaceService(settings)

    mysql_ok, mysql_msg = check_mysql_connectivity(settings)
    if not mysql_ok:
        alerts.append(
            WorkflowAlert("mysql_down", "error", "MySQL inaccessible", mysql_msg)
        )

    active = ws.get_active_mois()
    if mois and active and canonical_sme_mois_yyyy_mm(active) != canonical_sme_mois_yyyy_mm(mois):
        alerts.append(
            WorkflowAlert(
                "active_mismatch",
                "warn",
                "Mois actif incohérent",
                f"UI : « {mois} » — base : « {active} ».",
            )
        )

    edit = evaluate_editing_state(settings)
    if edit.locked:
        alerts.append(
            WorkflowAlert(
                "period_locked",
                "warn",
                "Saisie bloquée",
                edit.detail or "Période fermée dans sme_periode.",
            )
        )
    elif edit.tabsme_statut and "réouvert" in (edit.tabsme_statut or "").casefold():
        alerts.append(
            WorkflowAlert(
                "period_reopen",
                "info",
                "Réouverture exceptionnelle",
                f"Période « {edit.mois_actif} » en statut {edit.tabsme_statut}.",
            )
        )

    if mois and ws.is_month_closed(mois):
        alerts.append(
            WorkflowAlert(
                "viewing_closed",
                "info",
                "Mois clôturé affiché",
                f"« {mois} » est fermé — lecture historique MySQL.",
            )
        )

    if mois:
        fermes = ws.list_closed_months()
        if mois in fermes and not find_archive_for_month(mois):
            alerts.append(
                WorkflowAlert(
                    "no_archive",
                    "warn",
                    "Archive Access absente",
                    f"Aucun fichier Access pour {mois}. Consultez les fichiers d’archive : une sauvegarde MySQL peut être disponible séparément.",
                )
            )

    if find_latest_backup_zip() is None:
        alerts.append(
            WorkflowAlert(
                "no_backup",
                "warn",
                "Aucune sauvegarde complète trouvée",
                "Planifiez une sauvegarde complète (Système → Sauvegarde).",
            )
        )

    try:
        n_open = WorkflowTaskService(settings).count_open(mois=mois or None)
        if n_open:
            alerts.append(
                WorkflowAlert(
                    "open_tasks",
                    "info",
                    f"{n_open} tâche(s) ouverte(s)",
                    "Consultez la file de tâches (Gestion des mois et archives).",
                )
            )
    except Exception:
        alerts.append(WorkflowAlert(
            "tasks_unavailable", "warn", "Tâches non actualisées",
            "Impossible de charger les tâches workflow. Réessayez l'actualisation.",
        ))

    return tuple(alerts)
