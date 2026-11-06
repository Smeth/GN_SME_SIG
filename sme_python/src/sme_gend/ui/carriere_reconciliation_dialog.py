"""Dialogue : réconciliation carrière ↔ référentiels MySQL."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from sme_gend.config.settings import Settings
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.carriere_reconciliation import reconcile_carriere_referentiels
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.ui.bg_task import run_with_progress


def run_carriere_reconciliation_dialog(parent: QWidget | None, settings: Settings) -> None:
    ctx = load_sme_context()
    mois = canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or None
    mois_lbl = mois or "toute la base"

    from sme_gend.services.automatic_maintenance import prepare_call
    try:
        plan=prepare_call(settings,reconcile_carriere_referentiels,mois=mois,log_unresolved=True)
    except Exception as error:
        QMessageBox.critical(parent,"Reconciliation",str(error))
        return
    if not confirm_yes_no(
        parent,
        "Réconcilier référentiels carrière",
        "<b>Réconciliation carrière ↔ référentiels</b><br><br>"
        f"Périmètre : <b>{mois_lbl}</b> (mois actif de session, ou toute la base si absent).<br><br>"
        "Actions :<br>"
        "• Remplir les FK manquantes (<code>grade_id</code>, <code>structure_id</code>, …)<br>"
        "• Aligner <code>ordre_tri</code> / <code>presseance</code> sur "
        "<code>ref_grade.pref_ord</code><br>"
        "• Journaliser les FK non résolues dans <code>etl_erreur</code><br><br>"
        "Continuer ?",
        default_no=True,
        rich_text=True,
    ):
        return

    result = run_with_progress(
        parent,
        "Réconciliation carrière",
        "Analyse et correction des liens référentiels…",
        lambda: reconcile_carriere_referentiels(settings, mois=mois, log_unresolved=True, expected=plan),
    )
    if result is None:
        return

    ub = result.unresolved_before
    ua = result.unresolved_after
    QMessageBox.information(
        parent,
        "Réconciliation terminée",
        "Réconciliation carrière ↔ référentiels terminée.\n\n"
        f"• Lignes carrière mises à jour (FK) : {result.fk_rows_updated}\n"
        f"• ordre_tri / presseance alignés : {result.ordre_tri_updated}\n"
        f"• FK non résolues : {ub.total()} → {ua.total()}\n"
        f"  — grade : {ub.grade} → {ua.grade}\n"
        f"  — structure : {ub.structure} → {ua.structure}\n"
        f"• Erreurs journalisées (etl_erreur) : {result.etl_errors_logged}\n\n"
        "Complétez le référentiel grades/structures puis relancez si nécessaire.",
    )
