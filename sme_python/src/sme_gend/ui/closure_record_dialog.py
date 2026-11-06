"""Dossier de clôture construit depuis le journal central existant."""
from pathlib import Path
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QPlainTextEdit, QFileDialog, QMessageBox
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services.auth_guard import session_has_permission
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.pre_closure_report_dialog import format_control_report


def load_closure_record(settings, month):
    if not session_has_permission("workflow.pilotage"):
        raise PermissionError("Consultation non autorisée.")
    rows = AppConfigRepository(settings).search_audit_journal(module="system",
        action="sme_month_closure_piste_a", text=month, limit=2000, require_table=True)
    closures = [row for row in rows if row.get("payload", {}).get("mois_cloture") == month]
    imports = AppConfigRepository(settings).search_audit_journal(module="system",
        action="sme_month_import_existing_db", text=month, limit=2000, require_table=True)
    closures += [row for row in imports if row.get("payload", {}).get("mois_importe") == month
                 and row.get("payload", {}).get("tabsme_rows_closed", 0)]
    controls = AppConfigRepository(settings).search_audit_journal(module="workflow",
        action="sme_preclosure_report", sme_mois=month, limit=2000, require_table=True)
    return closures + controls


class ClosureRecordDialog(QDialog):
    def __init__(self, parent, settings, month):
        super().__init__(parent)
        self.setWindowTitle(f"Dossier de clôture — {month}")
        self.resize(900, 620)
        layout = QVBoxLayout(self)
        self._text = QPlainTextEdit("Chargement du journal central…")
        self._text.setReadOnly(True)
        layout.addWidget(self._text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        self._export = buttons.addButton("Exporter le dossier…", QDialogButtonBox.ButtonRole.ActionRole)
        self._export.setEnabled(False)
        self._export.clicked.connect(self._save)
        layout.addWidget(buttons)

        def loaded(rows):
            if not session_has_permission("workflow.pilotage"):
                self._text.setPlainText("Consultation non autorisée.")
                return
            text = f"Période : {month}\nSource : journal central\n\n"
            if not rows:
                text += "Aucune trace de clôture disponible. Cela ne prouve pas que cette période n'a jamais été clôturée."
            for row in rows:
                payload = row.get("payload", {})
                if row.get("action") == "sme_preclosure_report":
                    text += f"Contrôle enregistré le {row.get('ts_utc')} par {row.get('actor') or 'non renseigné'}\n"
                    text += format_control_report(payload.get("report") or {}) + "\n\n"
                    continue
                text += f"Clôture validée le {row.get('ts_utc')} par {row.get('actor') or 'non renseigné'}\n"
                text += f"Mois activé : {payload.get('mois_suivant', 'non renseigné')}\n"
                text += "Fichiers associés (chemins enregistrés lors de la clôture) :\n"
                for key, label in (("data_archive","Archive Access"),("layout_archive","Mise en page"),("mysql_archive","Sauvegarde MySQL")):
                    text += f"  {label} : {payload.get(key) or 'aucun'}\n"
                report = payload.get("precheck_report")
                text += "Contrôles :\n" + (format_control_report(report) if report else
                    "Rapport détaillé non conservé pour cette clôture ancienne.") + "\n\n"
            if len(rows) >= 2000:
                text += "Affichage limité aux 2 000 traces les plus récentes.\n"
            self._text.setPlainText(text)
            self._export.setEnabled(True)

        def failed(error):
            self._text.setPlainText("Journal indisponible. Fermez puis réessayez après vérification de la connexion.")
        run_in_background(self, lambda: load_closure_record(settings, month), loaded, on_error=failed)

    def _save(self):
        if not session_has_permission("workflow.pilotage"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter le dossier", "dossier_cloture.txt", "Texte (*.txt)")
        if path:
            from sme_gend.services.safe_paths import atomic_destination
            try:
                with atomic_destination(Path(path), replace_existing=True) as temporary:
                    temporary.write_text(self._text.toPlainText(), encoding="utf-8")
            except OSError:
                QMessageBox.warning(self, "Export", "Impossible d’enregistrer le dossier à cet emplacement.")
