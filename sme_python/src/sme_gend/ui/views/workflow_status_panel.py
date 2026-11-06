"""Alertes SME et file de tâches (page Pilotage du mois)."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QComboBox, QLineEdit, QPlainTextEdit, QSplitter,
    QInputDialog,
)

from sme_gend.config.settings import Settings
from sme_gend.services.workflow_alert_service import WorkflowAlert, build_workflow_alerts
from sme_gend.services.workflow_permissions import session_can_perform_action
from sme_gend.services.workflow_task_service import WorkflowTaskService, WorkflowTaskConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict
from copy import deepcopy
from sme_gend.services.search_text import search_values
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.services.auth_guard import session_has_permission


_ALERT_PAGE: dict[str, int] = {}
_LOG = logging.getLogger(__name__)


def configure_alert_navigation(page_map: dict[str, int]) -> None:
    """Enregistre la correspondance alerte → page (injecté depuis l'UI)."""
    global _ALERT_PAGE
    _ALERT_PAGE = dict(page_map)


class WorkflowStatusPanel(QFrame):
    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        navigate_to_page=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("workflowStatusPanel")
        self._settings = settings
        self._task_svc = WorkflowTaskService(settings)
        self._navigate = navigate_to_page
        self._busy, self._generation, self._fresh = False, 0, False
        self._tasks = []

        self._alerts_list = QListWidget()
        self._alerts_list.setWordWrap(True)
        self._alerts_list.setToolTip("Double-clic : ouvrir la page concernée")
        self._alerts_list.itemDoubleClicked.connect(self._on_alert_activated)
        self._tasks_list = QListWidget()
        self._tasks_list.setWordWrap(True)
        self._tasks_status = QLabel()
        self._tasks_status.setWordWrap(True)
        self._tasks_status.hide()

        self._btn_refresh = QPushButton("Actualiser alertes / tâches")
        self._btn_refresh.clicked.connect(self.reload_async)
        self._btn_resolve = QPushButton("Marquer comme traitée…")
        self._btn_resolve.clicked.connect(self._on_resolve_task)
        self._btn_resolve.setEnabled(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        title = QLabel("Alertes et tâches")
        title.setObjectName("dashPageTitle")
        title.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(title)
        intro = QLabel("Consultez les points à vérifier et les tâches partagées. Traiter une tâche enregistre son suivi ; cela ne corrige pas les données à sa place.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        filters = QHBoxLayout()
        self._search, self._month, self._priority = QLineEdit(), QComboBox(), QComboBox()
        self._search.setPlaceholderText("Rechercher une tâche, un mois ou un détail…")
        self._search.setClearButtonEnabled(True)
        self._month.addItem("Tous les mois", None)
        self._state = QComboBox()
        self._state.addItem("Tâches à traiter", "open")
        self._state.addItem("Tâches traitées", "done")
        self._state.addItem("À revalider", "review")
        self._state.addItem("Tous les états", None)
        for label, value in (("Toutes les priorités", ""), ("Haute", "high"), ("Normale", "normal"), ("Basse", "low")):
            self._priority.addItem(label, value)
        for widget in (self._search, self._month, self._priority, self._state):
            filters.addWidget(widget)
        lay.addLayout(filters)
        split = QSplitter(Qt.Orientation.Horizontal)
        alert_box, task_box = QWidget(), QWidget()
        alert_layout, task_layout = QVBoxLayout(alert_box), QVBoxLayout(task_box)
        alert_layout.addWidget(QLabel("Alertes opérationnelles"))
        self._alerts_empty = QLabel("Alertes non chargées.")
        self._alerts_empty.setWordWrap(True)
        alert_layout.addWidget(self._alerts_empty)
        alert_layout.addWidget(self._alerts_list, 1)
        alert_layout.addStretch(1)
        self._alert_layout = alert_layout
        self._task_count = QLabel("Tâches non chargées.")
        task_layout.addWidget(self._task_count)
        task_layout.addWidget(self._tasks_list, 1)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(120)
        self._detail.setPlaceholderText("Sélectionnez une tâche pour consulter ses détails.")
        task_layout.addWidget(self._detail)
        task_layout.addStretch(1)
        self._task_layout = task_layout
        split.addWidget(alert_box)
        split.addWidget(task_box)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        lay.addWidget(self._tasks_status)
        row = QHBoxLayout()
        row.addWidget(self._btn_refresh)
        row.addWidget(self._btn_resolve)
        self._btn_history = QPushButton("Journal de la tâche…")
        self._btn_history.clicked.connect(self._show_task_history)
        row.addWidget(self._btn_history)
        row.addStretch()
        lay.addLayout(row)
        self._tasks_list.currentItemChanged.connect(self._selection_changed)
        self._search.textChanged.connect(self._filter_tasks)
        self._month.currentIndexChanged.connect(self._filter_tasks)
        self._priority.currentIndexChanged.connect(self._filter_tasks)
        self._state.currentIndexChanged.connect(self._filter_tasks)
        self._alerts_list.hide()
        self._filter_tasks()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._task_svc = WorkflowTaskService(settings)
        self.mark_dirty()
        if self.isVisible():
            self.reload_async()

    def mark_dirty(self):
        self._generation += 1
        self._busy, self._fresh = False, False
        self._btn_refresh.setEnabled(True)
        self._tasks = []
        self._alerts_list.clear()
        self._filter_tasks()
        self._alerts_empty.setText("Alertes non actualisées.")
        self._alerts_empty.show()

    def on_page_activated(self):
        if not self._fresh:
            self.reload_async()

    def _selection_changed(self, *_):
        item = self._tasks_list.currentItem()
        task = item.data(int(Qt.ItemDataRole.UserRole)+1) if item else None
        self._detail.setPlainText((task.detail + (f"\n\nTraitée par : {task.resolved_by or 'non renseigné'}\nLe : {task.resolved_at or 'non renseigné'}" if task.status == "done" else "")) if task else "")
        if task and task.status == "review":
            self._detail.appendPlainText("\nCe constat n’a plus été détecté lors du dernier contrôle complet. Vérifiez sa résolution avant de marquer la tâche comme traitée.")
        self._btn_resolve.setText("Réactiver la tâche…" if task and task.status == "done" else "Marquer comme traitée…")
        self._btn_resolve.setEnabled(bool(task) and self._fresh and not self._busy and session_can_perform_action("workflow.resolve_task"))
        self._btn_history.setEnabled(bool(task) and self._fresh and not self._busy and session_has_permission("workflow.pilotage"))

    def _show_task_history(self):
        item = self._tasks_list.currentItem()
        if item is None or not self._fresh or not session_has_permission("workflow.pilotage"):
            return
        identifier = item.data(Qt.ItemDataRole.UserRole)
        settings, generation = self._settings, self._generation
        def work():
            from sme_gend.repositories.app_config_repository import AppConfigRepository
            rows = AppConfigRepository(settings).search_audit_journal(module="workflow", entity_id=str(identifier), limit=2000, require_table=True)
            return [row for row in rows if str(row.get("payload", {}).get("entity_id")) == str(identifier)]
        rows = run_with_progress(self, "Journal de la tâche", "Lecture des traces centrales…", work)
        if rows is None or generation != self._generation or not session_has_permission("workflow.pilotage"):
            return
        from PySide6.QtWidgets import QDialog, QDialogButtonBox
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Journal de la tâche {identifier}")
        dialog.resize(800, 520)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        lines = []
        labels = {"workflow_task_create":"Création", "workflow_task_resolve":"Traitement",
                  "workflow_task_reopen":"Réactivation", "workflow_task_update":"Actualisation du constat",
                  "workflow_task_revalidate":"Constat disparu : à revalider"}
        for row in rows:
            payload = row.get("payload", {})
            after = payload.get("after") or {}
            lines.append(f"{row.get('ts_utc', '')} — {row.get('actor') or 'non renseigné'}\n"
                         f"{labels.get(row.get('action'), 'Modification')}\n"
                         f"{after.get('detail') or ''}\n{after.get('comment') or ''}")
        if len(rows) >= 2000:
            lines.append("Affichage limité aux 2 000 traces les plus récentes.")
        text.setPlainText("\n\n".join(lines) if rows else "Aucune trace disponible.")
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _filter_tasks(self, *_):
        selected = self._tasks_list.currentItem()
        identifier = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self._tasks_list.clear()
        for task in self._tasks:
            if (self._state.currentData() and task.status != self._state.currentData()
                and not (self._state.currentData() == "open" and task.status == "review")
                or self._month.currentData() is not None and (task.mois or "") != self._month.currentData()
                or self._priority.currentData() and task.priority != self._priority.currentData()
                or not search_values(self._search.text(), task.title, task.detail, task.mois)):
                continue
            priority = {"high": "Haute", "normal": "Normale", "low": "Basse"}.get(task.priority, task.priority)
            state = {"done":"Traitée", "review":"À revalider", "open":"Ouverte"}.get(task.status, task.status)
            item = QListWidgetItem(f"{task.mois or 'Sans mois'} · {priority} · {state} — {task.title}")
            item.setToolTip(task.detail)
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            item.setData(int(Qt.ItemDataRole.UserRole)+1, deepcopy(task))
            self._tasks_list.addItem(item)
            if task.id == identifier:
                self._tasks_list.setCurrentItem(item)
        count = self._tasks_list.count()
        self._tasks_list.setVisible(count > 0)
        self._detail.setVisible(count > 0)
        self._task_layout.setStretch(self._task_layout.count()-1, 0 if count else 1)
        self._task_count.setText(f"{count} tâche(s) affichée(s) sur {len(self._tasks)}." if self._tasks else "Aucune tâche." if self._fresh else "Tâches non actualisées.")
        if self._tasks and not count:
            self._task_count.setText("Aucune tâche ne correspond aux filtres.")
        self._selection_changed()

    def reload_async(self):
        if self._busy or not session_has_permission("workflow.pilotage"):
            return
        self._busy = True
        self._fresh = False
        self._generation += 1
        generation, service, settings = self._generation, self._task_svc, self._settings
        self._btn_refresh.setEnabled(False)
        self._tasks_status.setText("Actualisation des alertes et tâches…")
        self._tasks_status.show()
        self._selection_changed()
        def loaded(result):
            if generation == self._generation:
                self._busy = False
                self._btn_refresh.setEnabled(True)
                if not session_has_permission("workflow.pilotage"):
                    self.mark_dirty()
                    self._tasks_status.setText("Consultation non autorisée.")
                    self._tasks_status.show()
                    return
                self._apply_result(*result)
        def failed(error):
            if generation == self._generation:
                self._busy = False
                self._btn_refresh.setEnabled(True)
                self._load_failed()
        run_in_background(self, lambda: (service.list_open(limit=None, status=None), build_workflow_alerts(settings)), loaded, on_error=failed)

    def _load_failed(self):
        self._fresh = False
        self._tasks_status.setText("Actualisation impossible. La liste précédente est conservée mais n’est pas à jour ; réessayez avec Actualiser.")
        self._tasks_status.show()
        self._selection_changed()

    def _apply_result(self, tasks, alerts):
        self._fresh = True
        self._tasks = list(tasks)
        self._alerts_list.clear()
        for alert in alerts:
            self._add_alert_item(alert)
        self._alerts_empty.setText("Aucune alerte opérationnelle détectée." if not alerts else "")
        self._alerts_empty.setVisible(not alerts)
        self._alerts_list.setVisible(bool(alerts))
        self._alert_layout.setStretch(self._alert_layout.count()-1, 0 if alerts else 1)
        month = self._month.currentData()
        self._month.blockSignals(True)
        self._month.clear()
        self._month.addItem("Tous les mois", None)
        for value in sorted({task.mois or "" for task in tasks}, reverse=True):
            self._month.addItem(value or "Sans mois", value)
        self._month.setCurrentIndex(max(0, self._month.findData(month)))
        self._month.blockSignals(False)
        self._filter_tasks()
        self._tasks_status.clear()
        self._tasks_status.hide()

    def set_navigate_to_page(self, fn) -> None:
        self._navigate = fn

    def reload(self) -> bool:
        if not session_has_permission("workflow.pilotage"):
            self.mark_dirty()
            return False
        try:
            tasks = self._task_svc.list_open(limit=None, status=None)
            alerts = build_workflow_alerts(self._settings)
        except Exception:
            _LOG.warning("Actualisation des alertes et tâches workflow impossible.")
            self._load_failed()
            return False
        self._apply_result(tasks, alerts)
        return True

    def _add_alert_item(self, al: WorkflowAlert) -> None:
        level = {"error": "Erreur", "warn": "À vérifier", "info": "Information"}.get(al.level, "Information")
        item = QListWidgetItem(f"[{level}] {al.title} — {al.detail}")
        item.setData(Qt.ItemDataRole.UserRole, al.alert_id)
        page = _ALERT_PAGE.get(al.alert_id)
        if page is not None and self._navigate is not None:
            item.setToolTip(f"Double-clic : ouvrir la page liée ({al.title})")
        self._alerts_list.addItem(item)

    def _on_alert_activated(self, item: QListWidgetItem) -> None:
        if self._navigate is None:
            return
        aid = item.data(Qt.ItemDataRole.UserRole)
        if not aid:
            return
        if str(aid) in ("open_tasks", "tasks_unavailable") and hasattr(self.window(), "_cycle_archives_view"):
            self.window()._cycle_archives_view.show_tasks_tab()
            return
        page = _ALERT_PAGE.get(str(aid))
        if page is not None:
            self._navigate(page)

    def _on_resolve_task(self) -> None:
        if self._busy or not self._fresh or not session_can_perform_action("workflow.resolve_task"):
            return
        current = self._tasks_list.currentItem()
        if current is None:
            return
        identifier = current.data(Qt.ItemDataRole.UserRole)
        if identifier is None:
            return
        expected = deepcopy(current.data(int(Qt.ItemDataRole.UserRole)+1))
        service, generation = self._task_svc, self._generation
        if not confirm_yes_no(self, "Traiter la tâche", "Confirmez que les vérifications nécessaires ont été effectuées. Le traitement sera journalisé.", default_no=True):
            return
        if generation != self._generation or not self._fresh:
            return
        comment, accepted = QInputDialog.getMultiLineText(self, "Suivi de la tâche",
            "Motif de réactivation :" if expected.status == "done" else "Commentaire facultatif (conservé dans le journal) :")
        if not accepted or (expected.status == "done" and not comment.strip()):
            return
        if generation != self._generation or not self._fresh or not session_can_perform_action("workflow.resolve_task"):
            self._tasks_status.setText("Le contexte a changé. Actualisez les tâches avant de poursuivre.")
            self._tasks_status.show()
            return
        def work():
            try:
                return service.resolve_task(int(identifier), expected=expected, comment=comment,
                                            reopen=expected.status == "done"), None
            except Exception as error:
                return None, error
        try:
            result = run_with_progress(self, "Traitement d’une tâche", "Enregistrement et contrôle de la version…", work)
            if result is None:
                return
            changed, error = result
            if error is not None:
                raise error
        except WorkflowTaskConflictError as exc:
            self._tasks_status.setText("Tâche modifiée sur un autre poste. Votre sélection est conservée ; actualisez la liste.")
            self._tasks_status.show()
            show_reference_conflict(self,exc)
            return
        except Exception:
            _LOG.warning("Traitement de la tâche workflow non confirmé.")
            message = "Le traitement de la tâche n'a pas pu être confirmé. Actualisez la liste avant de réessayer."
            self._tasks_status.setText(message)
            self._tasks_status.show()
            QMessageBox.critical(self,"Tâches workflow",message)
            return
        self.reload_async()
