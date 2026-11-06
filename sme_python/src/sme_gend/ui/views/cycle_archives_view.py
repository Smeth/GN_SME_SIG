"""Page unique : cycle mensuel SME + archives de clôture (onglets)."""

from __future__ import annotations

from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from sme_gend.ui.views.archives_view import ArchivesView
from sme_gend.ui.views.month_pilotage_view import MonthPilotageView


class CycleArchivesView(QWidget):
    """Cycle, suivi des tâches et fichiers : trois parcours, un seul module."""

    TAB_CYCLE = 0
    TAB_TASKS = 1
    TAB_ARCHIVES = 2

    def __init__(
        self,
        pilotage: MonthPilotageView,
        archives: ArchivesView,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._pilotage = pilotage
        self._archives = archives
        self._tabs = QTabWidget()
        self._tabs.setObjectName("cycleArchivesTabs")
        self._tabs.addTab(pilotage, "Cycle mensuel")
        self._tasks = pilotage._workflow_status
        self._tabs.addTab(self._tasks, "Alertes et tâches")
        self._tabs.addTab(archives, "Fichiers d'archive")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._tabs)

        self._apply_tab_permissions()
        self._tabs.currentChanged.connect(self._activate_tab)

    def _activate_tab(self, index):
        if not self._tabs.isTabVisible(index):
            return
        if index == self.TAB_CYCLE:
            self._pilotage.on_page_activated()
        elif index == self.TAB_TASKS:
            self._tasks.on_page_activated()
        elif index == self.TAB_ARCHIVES:
            self._archives.on_page_activated()

    def show_cycle_tab(self) -> None:
        if not self._tabs.isTabVisible(self.TAB_CYCLE):
            return
        self._tabs.setCurrentIndex(self.TAB_CYCLE)
        self._pilotage.on_page_activated()

    def show_archives_tab(self) -> None:
        if not self._tabs.isTabVisible(self.TAB_ARCHIVES):
            return
        self._tabs.setCurrentIndex(self.TAB_ARCHIVES)
        self._archives.on_page_activated()

    def show_tasks_tab(self) -> None:
        if self._tabs.isTabVisible(self.TAB_TASKS):
            self._tabs.setCurrentIndex(self.TAB_TASKS)
            self._tasks.on_page_activated()

    def on_page_activated(self) -> None:
        self._apply_tab_permissions()
        idx = self._tabs.currentIndex()
        self._activate_tab(idx)

    def mark_dirty(self) -> None:
        self._pilotage.mark_dirty()
        self._archives.mark_dirty()

    def mark_view_stale(self) -> None:
        self._pilotage.mark_view_stale()
        self._archives.mark_view_stale()

    def set_active_mois(self, mois: str | None) -> None:
        self._archives.set_active_mois(mois)

    def apply_runtime_settings(self, settings) -> None:
        self._pilotage.apply_runtime_settings(settings)
        self._archives.mark_view_stale()
        self._apply_tab_permissions()
        if self.isVisible():
            self._activate_tab(self._tabs.currentIndex())

    def _apply_tab_permissions(self) -> None:
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is None or not auth.is_enforced():
            can_cycle = can_arch = True
        else:
            can_cycle = auth.session_has_permission("workflow.pilotage")
            can_arch = auth.session_has_permission("system.archives")

        self._tabs.setTabVisible(self.TAB_CYCLE, can_cycle)
        self._tabs.setTabVisible(self.TAB_TASKS, can_cycle)
        self._tabs.setTabVisible(self.TAB_ARCHIVES, can_arch)
        self._tabs.tabBar().setVisible(can_cycle)

        cur = self._tabs.currentIndex()
        if not self._tabs.isTabVisible(cur):
            if can_cycle:
                self._tabs.setCurrentIndex(self.TAB_CYCLE)
            elif can_arch:
                self._tabs.setCurrentIndex(self.TAB_ARCHIVES)
