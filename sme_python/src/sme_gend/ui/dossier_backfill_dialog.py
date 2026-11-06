"""Dialogue : reconstruction des dossiers agents depuis l'historique MySQL."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from sme_gend.config.settings import Settings
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.services.agent_dossier_import_enrichment import (
    backfill_dossier_from_all_snapshots,
)
from sme_gend.ui.bg_task import run_with_progress


def run_backfill_dossier_dialog(parent: QWidget | None, settings: Settings) -> None:
    """
    Rejoue l'enrichissement diplômes / passations / affectations pour tous les agents
    à partir des snapshots carrière déjà en MySQL.
    """
    if not confirm_yes_no(
        parent,
        "Reconstruire les dossiers agents",
        "<b>Reconstruction des dossiers</b> à partir de l'historique MySQL "
        "(snapshots <code>carriere_situation</code> déjà importés).<br><br>"
        "Pour chaque agent et chaque changement de mois détecté :<br>"
        "• <b>Grade</b> → passation (DateGrade ou 1<sup>er</sup> jour du mois SME)<br>"
        "• <b>Structure</b> → affectation (1<sup>er</sup> jour du mois SME)<br>"
        "• <b>Diplôme</b> → ligne diplôme (DateObtention)<br><br>"
        "Les doublons existants sont ignorés. La base courante n'est pas effacée.<br><br>"
        "Continuer ?",
        default_no=True,
        rich_text=True,
    ):
        return

    result = run_with_progress(
        parent,
        "Reconstruction des dossiers",
        "Analyse des snapshots carrière…",
        lambda: backfill_dossier_from_all_snapshots(settings),
    )
    if result is None:
        return

    total = int(result.get("dossier_total") or 0)
    QMessageBox.information(
        parent,
        "Dossiers reconstruits",
        "Reconstruction terminée.\n\n"
        f"• Diplômes ajoutés : {result.get('dossier_diplomes', 0)}\n"
        f"• Passations ajoutées : {result.get('dossier_passations', 0)}\n"
        f"• Affectations ajoutées : {result.get('dossier_affectations', 0)}\n"
        f"• Total nouvelles lignes : {total}\n\n"
        "Ouvrez une fiche agent → onglet « Dossier complet » pour vérifier.",
    )
