"""Commande de vidage complet d'un mois, partagée entre menu et archives."""
import html

from PySide6.QtWidgets import QInputDialog, QMessageBox


def run_empty_month_dialog(parent, settings) -> bool:
    from sme_gend.ui.import_mois_dialog import pick_import_mois
    from sme_gend.ui.bg_task import run_with_progress
    from sme_gend.services.empty_sme_month import empty_sme_month, preview_empty_sme_month, can_empty_month

    if not can_empty_month():
        QMessageBox.warning(parent, "Vider un mois", "Cette action exige un administrateur autorisé à restaurer et supprimer les données.")
        return False
    mois = pick_import_mois(
        parent, title="Mois à nettoyer",
        description="Sélectionnez le <b>mois concerné</b>. Vous pourrez ensuite choisir "
                    "de retirer ses imports ou de vider toutes ses données rattachées.",
    )
    if not mois:
        return False
    modes = ["Retirer les données importées (conserver les saisies manuelles)",
             "Vider toutes les données rattachées au mois (saisies comprises)"]
    mode, ok = QInputDialog.getItem(parent, "Portée du vidage", "Choisissez les données à retirer :", modes, 0, False)
    if not ok:
        return False
    imported_only = mode == modes[0]
    try:
        preview = run_with_progress(parent, "Aperçu du vidage", "Comptage des données concernées…",
                                    lambda: preview_empty_sme_month(settings, mois, imported_only=imported_only))
    except Exception as exc:
        QMessageBox.critical(parent, "Aperçu impossible", str(exc))
        return False
    if preview is None:
        return False
    labels = {"carriere_situation": "Situations mensuelles / effectifs", "agent_diplome": "Diplômes",
              "agent_affectation": "Affectations", "agent_passation_grade": "Passations de grade",
              "agent_distinction": "Distinctions", "parcours_stage": "Fiches mensuelles de stages",
              "agent_mission": "Missions", "agent_malade": "Maladies", "agent_desertion": "Désertions",
              "agent_conge": "Congés", "mouvement_effectif": "Mouvements", "archives": "Copies d'archives",
              "workflow_task": "Tâches", "career_alert_ack": "Alertes acquittées",
              "career_alert_ack_occurrence": "Occurrences d'alertes", "import_lot": "Lots d'import",
              "etl_erreur": "Erreurs d'import", "agent_position_event": "Fiches de position"}
    summary = "\n".join(f"- {labels.get(k, k)} : {v}" for k, v in preview.items())
    text, accepted = QInputDialog.getText(
        parent, f"Vider {mois}",
        f"{mode}\n\nAperçu des suppressions :\n{summary}\n\n"
        "Les identités des agents, les autres mois et les événements importés encore attestés "
        "ailleurs sont conservés. La période reste disponible.\n"
        "Les nombres peuvent évoluer si un autre utilisateur modifie les données.\n"
        "Une sauvegarde sera créée avant la suppression.\n"
        f"Pour confirmer, saisissez exactement : {mois}",
    )
    if not accepted or text.strip() != mois:
        return False
    try:
        result = run_with_progress(parent, "Vider un mois", f"Vidage du mois {mois}…",
                                   lambda: empty_sme_month(settings, mois, imported_only=imported_only))
    except Exception as exc:
        QMessageBox.critical(parent, "Vider un mois", str(exc))
        return False
    if result is None:
        return False
    outcome = (f"Les données importées de {mois} ont été retirées ; les saisies manuelles sont conservées."
               if imported_only else f"Les données rattachées à {mois} ont été vidées.")
    details = f"{outcome}<br>Sauvegarde : {html.escape(result['backup'])}"
    details += f"<br>Situations mensuelles supprimées : {int(result.get('carriere_situation', 0))}."
    remaining = result.get('remaining_month_rows')
    if remaining is not None:
        details += f"<br>Situations encore rattachées au mois : {int(remaining.get('carriere_situation', 0))}."
    window = parent.window()
    display_month = getattr(window, '_display_mois', None)
    displayed = display_month() if callable(display_month) else None
    if isinstance(displayed, str) and displayed and displayed != mois:
        details += (f"<br><br>L’écran reste sur {html.escape(displayed)}. "
                    f"Sélectionnez {html.escape(mois)} pour consulter le mois nettoyé.")
    if result.get("archive_backup"):
        details += f"<br>Archives sauvegardées : {html.escape(result['archive_backup'])}"
    if result.get("warnings"):
        details += "<br><br>" + "<br>".join(html.escape(w) for w in result["warnings"])
    QMessageBox.information(parent, "Vidage terminé", details)
    if hasattr(window, "refresh_for_month_change"):
        window.refresh_for_month_change()
    return True
