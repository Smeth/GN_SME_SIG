"""Check maintenance before startup migrations or authentication writes."""


def wait_for_startup_maintenance(settings, parent=None):
    from PySide6.QtWidgets import QMessageBox
    from sme_gend.services.database_maintenance import maintenance_status

    while True:
        try:
            state = maintenance_status(settings)
        except Exception:
            QMessageBox.warning(parent, "Vérification MySQL",
                "Impossible de vérifier l'état de maintenance. Le démarrage est suspendu. "
                "Vérifiez la connexion et les droits de lecture MySQL.")
            return False
        if not state['active']:
            return True
        result = QMessageBox.warning(parent, "Maintenance centralisée",
            f"Base : {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}\n"
            f"Depuis : {state['started_at']} UTC\nMotif : {state['reason']}\n\n"
            "Les écritures sont suspendues. Attendez la fin de l'opération puis réessayez. "
            "Si elle a été interrompue, l'administrateur doit vérifier la sauvegarde et "
            "la récupération avant de lever la maintenance. Aucun verrou ne sera supprimé automatiquement.",
            QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if result != QMessageBox.StandardButton.Retry:
            return False
