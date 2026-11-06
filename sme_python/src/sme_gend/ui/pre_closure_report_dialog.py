"""Résultat lisible des contrôles, sans enregistrer de tâche ni fermer de mois."""
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHeaderView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QFileDialog, QMessageBox
from pathlib import Path
import csv
import io


def format_control_report(report):
    levels = {"ok":"Conforme", "warn":"À vérifier", "block":"Bloquant"}
    text = f"Mois contrôlé : {report.get('mois_cloture', 'non renseigné')} → {report.get('mois_suivant', 'non renseigné')}\n"
    for check in report.get("checks", []):
        text += f"\n[{levels.get(check.get('level'), 'Non renseigné')}] {check.get('title', '')}\n{check.get('detail', '')}\n"
    return text


def report_csv(report):
    stream = io.StringIO()
    writer = csv.writer(stream, delimiter=";", lineterminator="\n")
    writer.writerow(["Mois contrôlé", "Mois suivant", "État", "Contrôle", "Constat"])
    for check in report.checks:
        values = [report.mois_cloture, report.mois_suivant, check.level, check.title, check.detail]
        writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
    return stream.getvalue()


class PreClosureReportDialog(QDialog):
    def __init__(self, parent, report, settings=None):
        super().__init__(parent)
        self._report = report
        self.setWindowTitle("Contrôles avant clôture")
        self.resize(920, 560)
        layout = QVBoxLayout(self)
        title = QLabel(f"Mois contrôlé : {report.mois_cloture} → {report.mois_suivant}")
        title.setWordWrap(True)
        layout.addWidget(title)
        blocked = len(report.blocking)
        warnings = sum(check.level == "warn" for check in report.checks)
        summary = QLabel(f"{blocked} point(s) bloquant(s) · {warnings} avertissement(s). " +
                         ("Corrigez les points bloquants avant de clôturer." if blocked else "Les contrôles permettent de préparer la clôture ; la confirmation reste nécessaire."))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        table = QTableWidget(len(report.checks), 3)
        table.setHorizontalHeaderLabels(["État", "Contrôle", "Constat"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setWordWrap(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, check in enumerate(report.checks):
            for col, value in enumerate(({"ok": "Conforme", "warn": "À vérifier", "block": "Bloquant"}.get(check.level, check.level), check.title, check.detail)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, col, item)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        export = buttons.addButton("Exporter le rapport…", QDialogButtonBox.ButtonRole.ActionRole)
        export.clicked.connect(self._export)
        if settings is not None:
            from sme_gend.services.workflow_permissions import session_can_perform_action
            save = buttons.addButton("Conserver le contrôle et ses tâches…", QDialogButtonBox.ButtonRole.ActionRole)
            save.setEnabled(session_can_perform_action("workflow.close_month"))
            def store():
                if not session_can_perform_action("workflow.close_month"):
                    return
                from sme_gend.services.pre_closure_service import finalize_pre_closure_report
                from sme_gend.ui.bg_task import run_with_progress
                def work():
                    finalize_pre_closure_report(settings, report)
                    return True
                if run_with_progress(self, "Conserver le contrôle", "Enregistrement dans le journal central…", work):
                    save.setEnabled(False)
            save.clicked.connect(store)
        correction = buttons.addButton("Ouvrir l’écran de correction", QDialogButtonBox.ButtonRole.ActionRole)
        def open_correction():
            row = table.currentRow()
            if row < 0:
                return
            from sme_gend.ui.navigation_pages import PAGE_GAIN_PERTE, PAGE_LISTE_GENERALE, PAGE_SETTINGS, PAGE_BACKUP_RESTORE
            identifier = report.checks[row].check_id
            page = (PAGE_GAIN_PERTE if identifier.startswith(("gain_perte", "perte_")) else
                    PAGE_LISTE_GENERALE if identifier in ("liste_error", "empty_liste", "no_present") else
                    PAGE_BACKUP_RESTORE if identifier.startswith("backup") else
                    PAGE_SETTINGS if identifier == "mysql" else None)
            host = parent.window() if parent else None
            if page is not None and host is not None and hasattr(host, "_go_page"):
                self.accept()
                host._go_page(page)
            else:
                QMessageBox.information(self, "Correction", "Sélectionnez un contrôle relatif aux effectifs, mouvements, sauvegardes ou connexion. Les périodes se corrigent dans Cycle mensuel.")
        correction.clicked.connect(open_correction)
        layout.addWidget(buttons)

    def _export(self):
        from sme_gend.ui.document_job import require_export
        if not require_export(self):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Exporter les contrôles", f"controle_{self._report.mois_cloture}.csv", "CSV (*.csv)")
        if path:
            try:
                from sme_gend.services.safe_paths import atomic_destination
                with atomic_destination(Path(path), replace_existing=True) as temporary:
                    temporary.write_text(report_csv(self._report), encoding="utf-8-sig")
            except OSError:
                QMessageBox.warning(self, "Export", "Le rapport n’a pas pu être enregistré. Vérifiez l’emplacement et les droits.")
