"""Libellés partagés des commandes affichées dans plusieurs écrans."""

IMPORT_MONTH_ACCESS_ACTION = "Importer les données d’un mois (Access)…"
IMPORT_MONTH_TITLE = "Import des données d’un mois"
IMPORT_MONTH_DONE_TITLE = "Import des données du mois terminé"
IMPORT_MONTH_MENU_PATH = f"Fichier → Import & archives → {IMPORT_MONTH_ACCESS_ACTION}"

MONTH_MANAGEMENT_TITLE = "Gestion des mois et archives"
ADMIN_TOOLS_TITLE = "Administration et outils"
ADMIN_FOLLOWUP_TITLE = "Suivi administratif et activités"
REMOVED_RECORDS_TITLE = "Fiches retirées de la liste"
RETIREMENT_ATTESTATIONS_TITLE = "Attestations et suivi de la retraite"
CSV_UPDATE_IMPORT_TITLE = "Importer des mises à jour CSV (Édition SME)"
MONTH_ARCHIVE_TRANSFER_TITLE = "Transférer une archive mensuelle"
MONTH_ARCHIVE_RESTORE_TITLE = "Restaurer une archive mensuelle"
FULL_APP_RESTORE_ACTION = "Restaurer une sauvegarde complète de l’application…"

# Compatibilité d'affichage des catalogues de permissions déjà enregistrés.
# Les identifiants, attributions de droits et intitulés personnalisés distincts
# sont conservés. Édition SME et SME DPF restent inchangés à la demande de l'utilisateur.
UI_TITLE_RENAMES = {
    "Archives & clôture": MONTH_MANAGEMENT_TITLE,
    "Cycle & clôture": "Cycle mensuel",
    "Cycle & clôture SME": "Cycle mensuel",
    "Archives de clôture": "Fichiers d’archive",
    "Suivre les mouvements": ADMIN_FOLLOWUP_TITLE,
    "Mouvements": ADMIN_FOLLOWUP_TITLE,
    "Configurer": ADMIN_TOOLS_TITLE,
    "Personnel supprimé": REMOVED_RECORDS_TITLE,
    "Restaurer des fiches supprimées": "Restaurer des fiches retirées de la liste",
    "Limite d'âge": RETIREMENT_ATTESTATIONS_TITLE,
    "Import CSV / Excel": CSV_UPDATE_IMPORT_TITLE,
    "Importer une liste de personnel (CSV / Excel)": CSV_UPDATE_IMPORT_TITLE,
    "Sauvegarde / restauration": "Sauvegarde & restauration",
}


def display_ui_title(title: str) -> str:
    return UI_TITLE_RENAMES.get(title, title)
