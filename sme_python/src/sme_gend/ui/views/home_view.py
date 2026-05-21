"""Vue Accueil — tableau de bord (synthèse effectifs, TabSme, graphiques)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.views.dashboard_stats_panel import DashboardStatsPanel
from sme_gend.ui.views.sme_workflow_panel import SmeWorkflowPanel


class HomeView(QWidget):
    """Récapitulatif global : effectifs, TabSme, positions, graphiques."""

    def __init__(
        self,
        settings: Settings,
        dashboard: DashboardService,
        parent=None,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._dashboard = dashboard
        self._page_scroll: QScrollArea | None = None

        title = QLabel("Synthèse et pilotage")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Vue d'ensemble des effectifs, des positions, et du <b>cycle du mois</b> (période de travail, "
            "clôture, archives). Les détails techniques (fichiers, import) sont sous <b>Maintenance & fichiers</b>. "
            "<b>Recherche globale</b> : <b>Ctrl+K</b> (menus Affichage et Aide)."
        )
        sub.setObjectName("dashSubtitle")
        sub.setWordWrap(True)

        self._btn_refresh = QPushButton("Actualiser les indicateurs")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.clicked.connect(lambda: self._reload_stats(silent=False))

        head = QHBoxLayout()
        head.addWidget(title, alignment=Qt.AlignmentFlag.AlignLeft)
        head.addStretch()
        head.addWidget(self._btn_refresh)

        self._stats_panel = DashboardStatsPanel(
            settings,
            navigate_to_page=navigate_to_page,
            ensure_visible=self._ensure_visible,
        )

        self._sme_panel = SmeWorkflowPanel(
            settings,
            navigate_to_page=navigate_to_page,
            on_after_workflow=lambda: self._reload_stats(silent=True),
        )

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(20)
        inner_lay.addLayout(head)
        inner_lay.addWidget(sub)
        inner_lay.addWidget(self._stats_panel)
        inner_lay.addWidget(
            QLabel(
                "<b>Situation mensuelle (SME)</b> — mois de travail, TabSme et tendance",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._sme_panel)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self._page_scroll = scroll

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

        self._reload_stats(silent=True)

    def _ensure_visible(self, w: QWidget) -> None:
        if self._page_scroll is not None:
            self._page_scroll.ensureWidgetVisible(w)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._stats_panel.apply_runtime_settings(settings)
        self._sme_panel.apply_runtime_settings(settings)
        self._reload_stats(silent=True)

    def _reload_stats(self, *, silent: bool = False) -> None:
        s = run_with_progress(
            self,
            "Synthèse",
            "Chargement des indicateurs…",
            self._dashboard.snapshot,
            minimum_duration=600 if silent else 0,
        )
        if s is None:
            self._stats_panel.clear()
            return
        self._stats_panel.update(s)
        self._sme_panel.update_sme_table(s)
