"""Explication d'un constat et comparaison des identités concernées, sans mutation."""
from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QPlainTextEdit, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QPushButton, QDialogButtonBox, QMessageBox, QTabWidget, QWidget,
)
from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.services.auth_guard import require_permission
from sme_gend.services.personnel_lookup_service import PersonnelLookupService
from sme_gend.services.dossier_read_snapshot import load_dossier_read_snapshot
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog
from sme_gend.services.career_alert_provenance import load_career_provenance, describe_provenance


class CareerAlertDetailsDialog(QDialog):
    def __init__(self, parent, alert, settings, personnel, referentiel):
        super().__init__(parent)
        self.setWindowTitle("Comprendre et vérifier l’alerte")
        self.resize(1000, 720)
        self._alert = alert
        self._settings = replace(settings, mysql_view_mois=resolve_liste_view_mois(settings))
        self._personnel, self._referentiel = personnel, referentiel
        self._opening = False
        self._closed = False
        self._trace_loading = False
        self._trace_loaded = False
        layout = QVBoxLayout(self)
        title = QLabel(alert.label)
        title.setWordWrap(True)
        title.setStyleSheet("font-weight: bold; font-size: 17px;")
        layout.addWidget(title)
        message = alert.message.removesuffix(alert.identity_summary).rstrip() if alert.related_agents else alert.message
        for text in (f"Fiche concernée : {alert.noms or 'Nom non renseigné'} — identifiant {alert.agent_legacy_id}",
                     f"Référence : {alert.reference_label}", message):
            label = QLabel(text)
            from PySide6.QtCore import Qt
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)
        self.identity_assessment = QLabel(alert.identity_summary if alert.related_agents else "")
        self.identity_assessment.setWordWrap(True)
        self.identity_assessment.setTextFormat(Qt.TextFormat.PlainText)
        self.identity_assessment.setVisible(bool(alert.related_agents))
        self.identity_assessment.setStyleSheet("QLabel { background: #eef3ff; color: #183153; padding: 8px; border-radius: 5px; }")
        layout.addWidget(self.identity_assessment)
        comparisons = alert.identity_comparisons
        self.related_table = QTableWidget(0, 6)
        self.related_table.setHorizontalHeaderLabels([
            "Identifiant de fiche", "Nom", "Naissance", "Matricule militaire", "Matricule solde", "Évaluation de l’identité"])
        self.related_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.related_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.related_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.related_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.related_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.related_table.setMinimumHeight(150)
        self.related_table.setRowCount(len(alert.related_agents))
        for i, row in enumerate(alert.related_agents):
            assessment = comparisons.get(row.legacy_id)
            values = (row.legacy_id, row.noms or "—", row.date_naissance.strftime("%d/%m/%Y") if row.date_naissance else "—",
                      row.matricule_militaire or "—", row.matricule_solde or "—",
                      "Fiche de référence" if row.legacy_id == alert.agent_legacy_id else
                      (assessment.label if assessment else "Comparaison indisponible"))
            for j, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(assessment.explanation if j == 5 and assessment else value)
                self.related_table.setItem(i, j, item)
        if alert.related_agents:
            layout.addWidget(QLabel("Fiches à comparer — identités actuellement enregistrées :"))
            layout.addWidget(self.related_table)
        else:
            self.related_table.hide()
        self.comparison_detail = QLabel()
        self.comparison_detail.setWordWrap(True)
        self.comparison_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.comparison_detail.setVisible(bool(alert.related_agents))
        layout.addWidget(self.comparison_detail)
        self.open_button = QPushButton("Ouvrir la fiche sélectionnée (consultation)")
        self.open_button.setVisible(bool(alert.related_agents))
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self._open_related)
        self.related_table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.open_button)
        from sme_gend.services.auth_guard import session_has_admin_role, session_can_modify_personnel, session_can_delete_personnel
        self.merge_button = QPushButton("Rapprocher deux fiches et rattacher leurs historiques…")
        self._can_offer_merge = bool(alert.merge_candidates) and session_has_admin_role() and session_can_modify_personnel() and session_can_delete_personnel()
        self.merge_button.hide()
        self.merge_button.clicked.connect(self._merge)
        layout.addWidget(self.merge_button)
        if alert.related_agents:
            selected = next((i for i, r in enumerate(alert.related_agents) if r.legacy_id != alert.agent_legacy_id), 0)
            self.related_table.selectRow(selected)
        self.trace_tabs = QTabWidget()
        guidance_page = QWidget()
        guidance_layout = QVBoxLayout(guidance_page)
        guidance_layout.addWidget(QLabel("Comment vérifier et résoudre ce constat :"))
        self.guidance = QPlainTextEdit()
        self.guidance.setReadOnly(True)
        self.guidance.setPlainText(alert.resolution_guidance)
        guidance_layout.addWidget(self.guidance)
        self.trace_tabs.addTab(guidance_page, "Comment vérifier")
        trace_page = QWidget()
        trace_layout = QVBoxLayout(trace_page)
        self.trace_status = QLabel("Dates, mois de présence et origine : lecture des traces existantes uniquement.")
        self.trace_status.setWordWrap(True)
        trace_layout.addWidget(self.trace_status)
        self.trace_refresh = QPushButton("Charger / actualiser les traces")
        self.trace_refresh.clicked.connect(self._load_provenance)
        trace_layout.addWidget(self.trace_refresh)
        self.trace_text = QPlainTextEdit()
        self.trace_text.setReadOnly(True)
        self.trace_text.setPlainText("Ouvrez cet onglet pour consulter les mois, les dates et les sources des fiches.")
        trace_layout.addWidget(self.trace_text)
        self.trace_tabs.addTab(trace_page, "Historique et provenance des dossiers")
        self.trace_tabs.currentChanged.connect(
            lambda index: self._load_provenance() if index == 1 and not self._trace_loaded else None)
        layout.addWidget(self.trace_tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selection_changed(self):
        index = self.related_table.currentRow()
        self.open_button.setEnabled(not self._opening and index >= 0)
        identity = self._alert.related_agents[index] if 0 <= index < len(self._alert.related_agents) else None
        assessment = self._alert.identity_comparisons.get(identity.legacy_id) if identity else None
        self.comparison_detail.setText(
            f"Fiche {identity.legacy_id} : {assessment.explanation}" if assessment else
            "Sélectionnez une autre fiche pour consulter ses concordances et divergences avec la référence.")
        eligible = self._can_offer_merge and assessment is not None and assessment.eligible
        self.merge_button.setVisible(eligible)
        self.merge_button.setEnabled(eligible and not self._opening and not self._trace_loading)
        if eligible:
            self.merge_button.setText(f"Examiner le rapprochement des fiches {self._alert.agent_legacy_id} et {identity.legacy_id}…")

    def _load_provenance(self):
        if self._trace_loading or not require_permission(self, "effectifs.liste"):
            return
        self._trace_loading = True
        self._selection_changed()
        self.trace_refresh.setEnabled(False)
        self.trace_status.setText("Lecture des mois, des lots d’import et du journal…")
        ids = [r.legacy_id for r in self._alert.related_agents] or [self._alert.agent_legacy_id]

        def show(report):
            self._trace_loading = False
            if self._closed:
                return
            self.trace_refresh.setEnabled(True)
            self._selection_changed()
            if not require_permission(self, "effectifs.liste"):
                self.trace_text.clear()
                self.trace_status.setText("Consultation non autorisée.")
                return
            self._trace_loaded = True
            self.trace_text.setPlainText(describe_provenance(report))
            self.trace_status.setText(report.presence_text() +
                (" Attention : certaines traces sont indisponibles ; voir le relevé." if report.warnings else ""))

        def fail(error):
            self._trace_loading = False
            if not self._closed:
                self._selection_changed()
                self.trace_refresh.setEnabled(True)
                self.trace_status.setText("Lecture impossible : aucune conclusion sur l’origine ou la présence mensuelle.")
                self.trace_text.setPlainText(str(error))

        run_in_background(self, lambda: load_career_provenance(self._settings, ids), show, on_error=fail)

    def _open_related(self):
        index = self.related_table.currentRow()
        if self._opening or index < 0 or not require_permission(self, "effectifs.liste"):
            return
        legacy_id = self._alert.related_agents[index].legacy_id
        self._opening = True
        self._selection_changed()
        self.open_button.setEnabled(False)
        self.open_button.setText("Préparation du dossier…")

        def prepare():
            pack = PersonnelLookupService(self._settings).resolve_dossier_for_legacy_id(legacy_id)
            if pack is None:
                return None
            row, context = pack
            return row, context, load_dossier_read_snapshot(self._settings, row)

        def reset():
            self._opening = False
            self._selection_changed()
            self.open_button.setText("Ouvrir la fiche sélectionnée (consultation)")
            self.open_button.setEnabled(self.related_table.currentRow() >= 0)

        def show(pack):
            reset()
            if self._closed or not require_permission(self, "effectifs.liste"):
                return
            if pack is None:
                QMessageBox.information(self, "Dossier", "Aucune fiche trouvée pour cet identifiant.")
                return
            row, context, snapshot = pack
            result = open_personnel_dossier_dialog(self, row, self._referentiel, self._personnel,
                allow_edit=False, historical_context=context, refresh_from_db=False, readonly_snapshot=snapshot)
            if result.liste_generale_changed is True:
                self.accept()

        def fail(error):
            reset()
            if not self._closed:
                QMessageBox.warning(self, "Dossier", str(error))

        run_in_background(self, prepare, show, on_error=fail)

    def done(self, result):
        self._closed = True
        super().done(result)

    def _merge(self):
        from sme_gend.ui.personnel_merge_dialog import PersonnelMergeDialog
        from sme_gend.services.auth_guard import session_has_admin_role, session_can_modify_personnel, session_can_delete_personnel
        candidates = self._alert.merge_candidates
        if not candidates or self._opening or self._trace_loading or not session_has_admin_role() or not session_can_modify_personnel() or not session_can_delete_personnel():
            return
        index = self.related_table.currentRow()
        selected = self._alert.related_agents[index] if 0 <= index < len(self._alert.related_agents) else None
        assessment = self._alert.identity_comparisons.get(selected.legacy_id) if selected else None
        if assessment is None or not assessment.eligible:
            return
        pair = tuple(row for row in candidates if row.legacy_id in {self._alert.agent_legacy_id, selected.legacy_id})
        dialog = PersonnelMergeDialog(self, self._settings, pair, self._alert.agent_legacy_id)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.accept()


def show_career_alert_details(parent, alert, settings, personnel, referentiel):
    if require_permission(parent, "effectifs.liste"):
        return CareerAlertDetailsDialog(parent, alert, settings, personnel, referentiel).exec()
