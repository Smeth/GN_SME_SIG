"""Consultation centrale des alertes détectées par le moteur carrière."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QInputDialog, QMessageBox,
)

from sme_gend.services import career_alerts_cache as cache
from sme_gend.services.career_import_alerts import acknowledge_alert
from sme_gend.services.career_import_alerts import list_career_acknowledgements, reopen_career_alert
from sme_gend.services.dossier_read_snapshot import load_dossier_read_snapshot
from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from dataclasses import replace
from sme_gend.services.auth_guard import session_has_permission, require_permission
from sme_gend.ui.bg_task import run_in_background
from sme_gend.services.personnel_lookup_service import PersonnelLookupService
from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog
from sme_gend.ui.career_alert_details_dialog import show_career_alert_details
from sme_gend.services.career_import_alerts import ALERT_LABELS
from sme_gend.services.search_text import search_values


class CareerAlertsView(QWidget):
    def __init__(self, settings, personnel, referentiel, parent=None):
        super().__init__(parent)
        self._settings, self._personnel, self._referentiel = settings, personnel, referentiel
        self._alerts = []
        self._visible = []
        self._busy = False
        self._generation = 0
        self._reload_pending = False
        self._records = []
        self._selection_to_restore = None
        self._display_mois = resolve_liste_view_mois(settings) or ""
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Alertes parcours"))
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un agent, identifiant, code ou message…")
        self.severity = QComboBox()
        for label, value in (("Toutes les gravités", ""), ("Avertissement", "warning"), ("Information", "info")):
            self.severity.addItem(label, value)
        self.code = QComboBox()
        self.code.addItem("Tous les types", "")
        self.month = QComboBox()
        self.month.addItem("Tous les mois", "*")
        self.refresh = QPushButton("Actualiser")
        self.state = QComboBox()
        self.state.addItem("Non acquittées", "active")
        self.state.addItem("Acquittements", "acknowledged")
        self.reference = QComboBox()
        self.reference.addItem("Toutes les références", "")
        self.reference.addItem("Parcours historique", "historical")
        self.reference.addItem("Contrôles actuels", "current")
        for widget in (self.search, self.severity, self.code, self.month, self.refresh):
            bar.addWidget(widget)
        layout.addLayout(bar)
        scope_bar = QHBoxLayout()
        scope_bar.addWidget(self.state)
        scope_bar.addWidget(self.reference)
        layout.addLayout(scope_bar)
        self.status = QLabel()
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Gravité", "Agent", "Identifiant", "Mois", "Type", "Message"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.open = QPushButton("Ouvrir le dossier")
        self.ack = QPushButton("Acquitter…")
        self.details = QPushButton("Comprendre / comparer…")
        actions.addWidget(self.details)
        actions.addWidget(self.open)
        actions.addWidget(self.ack)
        layout.addLayout(actions)
        self.search.textChanged.connect(self._filter)
        for combo in (self.severity, self.code, self.month):
            combo.currentIndexChanged.connect(self._filter)
        self.refresh.clicked.connect(self.reload)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.open.clicked.connect(self._open)
        self.details.clicked.connect(self._details)
        self.table.cellDoubleClicked.connect(lambda *_: QTimer.singleShot(0, self._details if self.state.currentData() == "active" else self._open))
        self.ack.clicked.connect(self._ack)
        self.state.currentIndexChanged.connect(self._state_changed)
        self.reference.currentIndexChanged.connect(self._filter)
        self._buttons()

    def on_page_activated(self):
        self.reload()

    def apply_runtime_settings(self, settings):
        self._settings = settings
        self._selection_to_restore = None
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self._display_mois = resolve_liste_view_mois(settings) or ""
        self.mark_dirty()
        if self.isVisible():
            self.reload()

    def mark_dirty(self):
        self._generation += 1
        self._reload_pending = True
        cache.invalidate_career_alerts_cache(self._settings)
        self._alerts = []
        self._records = []
        self._filter()

    def _state_changed(self):
        self._generation += 1
        self.search.setPlaceholderText(
            "Rechercher un agent, un type, un auteur ou un commentaire…"
            if self.state.currentData() == "acknowledged"
            else "Rechercher un agent, identifiant, code ou message…")
        self._alerts = []
        self._records = []
        self._filter()
        self.reload()

    def _launch(self, operation, success, *, label, preserve_on_error=False):
        """Fige le mois et ignore une livraison après changement de contexte."""
        if self._busy:
            return
        month = resolve_liste_view_mois(self._settings) or ""
        settings = replace(self._settings, mysql_view_mois=month or None)
        generation = self._generation
        self._busy = True
        self.status.setText(label)
        self._buttons()

        def finished(value):
            self._busy = False
            if not session_has_permission("effectifs.liste"):
                self._alerts = []
                self._records = []
                self._filter()
                self.status.setText("Consultation non autorisée.")
                return
            current = resolve_liste_view_mois(self._settings) or ""
            if generation != self._generation or month != current or self._reload_pending:
                self._reload_pending = False
                self.reload()
                return
            self._display_mois = month
            self._buttons()
            success(value)

        def failed(error):
            self._busy = False
            if generation != self._generation or self._reload_pending or month != (resolve_liste_view_mois(self._settings) or ""):
                self._reload_pending = False
                self.reload()
                return
            if preserve_on_error:
                self.status.setText("Opération non confirmée. Actualisez avant de réessayer.")
                self._buttons()
                QMessageBox.warning(self, "Alertes parcours", str(error))
            else:
                self._failed(error)

        run_in_background(self, lambda: operation(settings), finished, on_error=failed)

    def reload(self):
        if self._busy:
            self._reload_pending = True
            return
        if not require_permission(self, "effectifs.liste"):
            return
        self._reload_pending = False
        if self.state.currentData() == "acknowledged":
            self._launch(list_career_acknowledgements, self._loaded_history, label="Chargement des acquittements…")
        else:
            self._launch(cache.scan_and_cache_career_alerts, self._loaded, label="Recherche des alertes…")

    def _loaded(self, snapshot):
        self._busy = False
        if not snapshot.ready:
            self._alerts = []
            self._filter()
            self.status.setText("Scan en cours dans une autre vue. Actualisez dans quelques instants.")
            return
        self._alerts = list(snapshot.alerts)
        for combo, values, label in (
            (self.code, sorted({a.code for a in self._alerts}), "Tous les types"),
            (self.month, sorted({a.mois for a in self._alerts}, reverse=True), "Tous les mois"),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, "*" if combo is self.month else "")
            for value in values:
                combo.addItem(ALERT_LABELS.get(value, value) if combo is self.code else (value or "Sans mois"), value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self._filter()

    def _loaded_history(self, records):
        self._records = list(records)
        for combo, values, label in (
            (self.code, sorted({r.code for r in records}), "Tous les types"),
            (self.month, sorted({r.mois for r in records}, reverse=True), "Tous les mois"),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, "*" if combo is self.month else "")
            for value in values:
                combo.addItem(ALERT_LABELS.get(value, value) if combo is self.code else (value or "Sans mois"), value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self._filter()

    def _failed(self, error):
        self._busy = False
        self._alerts = []
        self._records = []
        self._filter()
        self.status.setText("Chargement impossible. Actualisez pour réessayer.")
        self._buttons()
        QMessageBox.warning(self, "Alertes parcours", str(error))

    def _filter(self, *_):
        if self.state.currentData() == "acknowledged":
            self._filter_history()
            return
        self._remember_selection()
        query = self.search.text().strip()
        self._visible = [a for a in self._alerts
                         if (not self.severity.currentData() or a.severity == self.severity.currentData())
                         and (not self.code.currentData() or a.code == self.code.currentData())
                         and (self.month.currentData() == "*" or a.mois == self.month.currentData())
                         and (not self.reference.currentData()
                              or (self.reference.currentData() == "current") == ("hors historique" in a.reference_label))
                         and search_values(query, a.noms, a.agent_legacy_id, a.code, a.label, a.message, a.mois)]
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["Gravité", "Agent", "Identifiant", "Mois du constat", "Type", "Message", "Référence des données"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setResizeContentsPrecision(100)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(self._visible))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        for i, a in enumerate(self._visible):
            for j, value in enumerate(("Avertissement" if a.severity == "warning" else "Information",
                                       a.noms or "—", a.agent_legacy_id, a.mois or "Actuel", a.label, a.message, a.reference_label)):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if j == 0:
                    item.setData(Qt.ItemDataRole.UserRole, self._record_key(a))
                self.table.setItem(i, j, item)
        self._restore_selection()
        month = self._display_mois or "non défini"
        self.status.setText(f"{len(self._visible)} alerte(s) affichée(s) / {len(self._alerts)} — historique jusqu’à {month}. "
                            "Les contrôles actuels ne reconstituent pas l’identité à cette date. Toutes les alertes non acquittées sont recherchées.")
        self.status.setWordWrap(True)
        self._buttons()

    def _filter_history(self):
        self._remember_selection()
        query = self.search.text().strip()
        self._visible = [r for r in self._records
            if (not self.code.currentData() or r.code == self.code.currentData())
            and (self.month.currentData() == "*" or r.mois == self.month.currentData())
            and search_values(query, r.noms, r.agent_legacy_id, r.code, ALERT_LABELS.get(r.code, r.code), r.by, r.note)]
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(["Agent (identité actuelle)", "Identifiant", "Mois du constat", "Type", "Acquitté par", "Date enregistrée", "Commentaire", "Version"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setResizeContentsPrecision(100)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(self._visible))
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        for i, r in enumerate(self._visible):
            for j, value in enumerate((r.noms or "—", r.agent_legacy_id, r.mois or "Actuel", ALERT_LABELS.get(r.code, r.code),
                                       r.by or "—", r.at, r.note or "—",
                                       "Constat précis" if r.fingerprint else "Ancien — ne masque plus d’alerte")):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if j == 0:
                    item.setData(Qt.ItemDataRole.UserRole, self._record_key(r))
                self.table.setItem(i, j, item)
        self._restore_selection()
        self.status.setText(f"{len(self._visible)} acquittement(s). Historique de traitement toutes périodes. "
                            "Réactiver retire l’acquittement ; l’alerte réapparaît seulement si le constat existe encore. "
                            "Les réactivations restent tracées dans le journal d’audit.")
        self._buttons()

    @staticmethod
    def _record_key(record):
        fingerprint = record.fingerprint
        return (type(record).__name__, record.agent_legacy_id, record.code, record.mois,
                fingerprint() if callable(fingerprint) else fingerprint)

    def _remember_selection(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is not None:
            self._selection_to_restore = tuple(item.data(Qt.ItemDataRole.UserRole))

    def _restore_selection(self):
        for index, record in enumerate(self._visible):
            if self._record_key(record) == self._selection_to_restore:
                self.table.selectRow(index)
                break

    def _selected(self):
        row = self.table.currentRow()
        return self._visible[row] if 0 <= row < len(self._visible) else None

    def _buttons(self):
        selected = self._selected() is not None and not self._busy
        self.open.setEnabled(selected and session_has_permission("effectifs.liste"))
        self.details.setEnabled(selected and self.state.currentData() == "active" and session_has_permission("effectifs.liste"))
        self.ack.setEnabled(selected and session_has_permission("workflow.resolve_task"))
        historical = self.state.currentData() == "acknowledged"
        self.ack.setText("Réactiver…" if historical else "Acquitter…")
        if historical and selected and not self._selected().fingerprint:
            self.ack.setEnabled(False)
        self.severity.setEnabled(not historical and not self._busy)
        self.reference.setEnabled(not historical and not self._busy)
        self.refresh.setEnabled(not self._busy)

    def _open(self):
        alert = self._selected()
        if not alert or self._busy or not require_permission(self, "effectifs.liste"):
            return
        def prepare(settings):
            pack = PersonnelLookupService(settings).resolve_dossier_for_legacy_id(alert.agent_legacy_id)
            if pack is None:
                return None
            row, context = pack
            return row, context, load_dossier_read_snapshot(settings, row)

        def display(pack):
            if not require_permission(self, "effectifs.liste"):
                return
            if pack is None:
                QMessageBox.information(self, "Dossier", "Aucune fiche trouvée pour cet agent.")
                return
            row, context, snapshot = pack
            result = open_personnel_dossier_dialog(
                self, row, self._referentiel, self._personnel, allow_edit=False,
                historical_context=context, refresh_from_db=False,
                readonly_snapshot=snapshot,
            )
            if result.liste_generale_changed is True:
                self.mark_dirty()
                self.reload()
        self._launch(prepare, display, label="Préparation du dossier…")

    def _details(self):
        alert = self._selected()
        if alert is not None and not self._busy and self.state.currentData() == "active":
            from PySide6.QtWidgets import QDialog
            result = show_career_alert_details(self, alert, self._settings, self._personnel, self._referentiel)
            if result == QDialog.DialogCode.Accepted:
                self.mark_dirty()
                self.reload()

    def _ack(self):
        alert = self._selected()
        if not alert or self._busy or not require_permission(self, "workflow.resolve_task"):
            return
        if self.state.currentData() == "acknowledged":
            if alert.fingerprint and confirm_yes_no(self, "Réactiver l’alerte", "Retirer cet acquittement ? Cette action sera journalisée."):
                self._launch(lambda settings: reopen_career_alert(settings, alert), self._mutation_done,
                             label="Réactivation…", preserve_on_error=True)
            return
        note, accepted = QInputDialog.getMultiLineText(self, "Acquitter l’alerte", alert.message + "\n\nCommentaire facultatif :")
        if not accepted:
            return
        if len(note.strip()) > 255:
            QMessageBox.warning(self, "Acquittement", "Le commentaire est limité à 255 caractères.")
            return
        self._launch(lambda settings: acknowledge_alert(settings, alert.agent_legacy_id, alert.code, alert.mois,
                     fingerprint=alert.fingerprint(), note=note, alert=alert), self._mutation_done,
                     label="Acquittement…", preserve_on_error=True)

    def _mutation_done(self, _):
        cache.invalidate_career_alerts_cache()
        self.reload()
