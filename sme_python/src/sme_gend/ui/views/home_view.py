"""Vue Accueil — tableau de bord (synthèse effectifs, graphiques)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
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


class HomeView(QWidget):
    """Synthèse statistique : effectifs, positions, grades et graphiques."""

    def __init__(
        self,
        settings: Settings,
        dashboard: DashboardService,
        parent=None,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
        navigate_gain_perte: Callable[[str, str | None], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._dashboard = dashboard
        self._page_scroll: QScrollArea | None = None

        title = QLabel("Tableau de bord")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Vue d'ensemble des effectifs et positions administratives "
            "(alignée sur le <b>mois affiché</b> dans la barre latérale). "
            "Le <b>pilotage du mois</b> (clôture, alertes) est dans la barre latérale. "
            "<b>Recherche personnel</b> : <b>Ctrl+K</b>."
        )
        sub.setObjectName("dashSubtitle")
        sub.setWordWrap(True)
        self._sub = sub

        self._btn_refresh = QPushButton("Actualiser les indicateurs")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.clicked.connect(self._on_refresh_clicked)

        head = QHBoxLayout()
        head.addWidget(title, alignment=Qt.AlignmentFlag.AlignLeft)
        head.addStretch()
        head.addWidget(self._btn_refresh)

        self._stats_panel = DashboardStatsPanel(
            settings,
            navigate_to_page=navigate_to_page,
            navigate_gain_perte=navigate_gain_perte,
            ensure_visible=self._ensure_visible,
        )

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(20)
        inner_lay.addLayout(head)
        inner_lay.addWidget(sub)
        inner_lay.addWidget(self._stats_panel)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self._page_scroll = scroll

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

        self._stats_dirty = True
        self._shown_epoch = -1
        self._reload_generation = 0
        QTimer.singleShot(80, self._deferred_initial_load)

    def _deferred_initial_load(self) -> None:
        win = self.window()
        if getattr(win, "_home_bootstrapping", False):
            QTimer.singleShot(250, self._deferred_initial_load)
            return
        if self._stats_dirty:
            self._reload_stats(silent=True)

    def mark_dirty(self) -> None:
        self._stats_dirty = True

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def _snapshot_is_stale(self) -> bool:
        from sme_gend.services.liste_generale_cache import dashboard_snapshot_epoch

        return self._stats_dirty or dashboard_snapshot_epoch() != self._shown_epoch

    def on_page_activated(self) -> None:
        if self._snapshot_is_stale():
            self._reload_stats(silent=True)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._snapshot_is_stale():
            self._reload_stats(silent=True)

    def _on_dashboard_load_error(self, exc: BaseException) -> None:
        self._stats_dirty = True
        import logging

        logging.getLogger(__name__).error(
            "Tableau de bord — chargement: %s", exc, exc_info=True
        )
        if self._shown_epoch < 0:
            self._stats_panel.clear()

    def _on_refresh_clicked(self) -> None:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache()
        self._reload_stats(silent=False)

    def _ensure_visible(self, w: QWidget) -> None:
        if self._page_scroll is not None:
            self._page_scroll.ensureWidgetVisible(w)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._stats_panel.apply_runtime_settings(settings)
        self._stats_dirty = True
        self._reload_stats(silent=True)

    def _reload_stats(self, *, silent: bool = False) -> None:
        self._reload_generation += 1
        generation = self._reload_generation

        def _apply(s, *, schedule_deferred: bool) -> None:
            if generation != self._reload_generation or s is None:
                return
            from sme_gend.services.liste_generale_cache import dashboard_snapshot_epoch

            try:
                self._stats_panel.update(s)
                self._update_scope_subtitle(s)
            except Exception:
                self._stats_dirty = True
                import logging

                logging.getLogger(__name__).exception("Tableau de bord — mise à jour indicateurs")
                if self._shown_epoch < 0:
                    self._stats_panel.clear()
                return
            self._stats_dirty = False
            self._shown_epoch = dashboard_snapshot_epoch()
            if schedule_deferred:
                self._reload_deferred(generation)

        if silent:
            from sme_gend.ui.bg_task import run_in_background

            def _on_quick(s) -> None:
                if s is None:
                    if self._shown_epoch < 0:
                        self._stats_panel.clear()
                    return
                _apply(s, schedule_deferred=True)

            run_in_background(
                self,
                self._dashboard.snapshot,
                _on_quick,
                on_error=self._on_dashboard_load_error,
            )
            return

        s = run_with_progress(
            self,
            "Synthèse",
            "Chargement des indicateurs…",
            self._dashboard.snapshot,
            silent=False,
        )
        if s is None:
            if self._shown_epoch < 0:
                self._stats_panel.clear()
            return
        _apply(s, schedule_deferred=True)

    def _reload_deferred(self, generation: int) -> None:
        from sme_gend.ui.bg_task import run_in_background

        def _on_full(s) -> None:
            if generation != self._reload_generation or s is None:
                return
            from sme_gend.services.liste_generale_cache import dashboard_snapshot_epoch

            try:
                self._stats_panel.update(s)
            except Exception:
                self._stats_dirty = True
                import logging

                logging.getLogger(__name__).exception(
                    "Tableau de bord — indicateurs complémentaires"
                )
                return
            self._stats_dirty = False
            self._shown_epoch = dashboard_snapshot_epoch()

        run_in_background(
            self,
            lambda: self._dashboard.snapshot(include_deferred=True),
            _on_full,
            on_error=self._on_dashboard_load_error,
        )

    def _update_scope_subtitle(self, s) -> None:
        ym = (getattr(s, "view_mois", None) or "").strip()
        if not ym:
            self._sub.setText(
                "Vue d'ensemble — <span style='color:#e65100;'>"
                "<b>aucun mois SME défini</b></span>. "
                "Configurez le mois dans la barre latérale (Piloter le mois SME → Gestion des mois et archives)."
            )
            return
        if getattr(s, "is_archive_view", False):
            self._sub.setText(
                f"Indicateurs pour le mois consulté <b>{ym}</b> "
                f"(historique MySQL — pas le mois de travail opérationnel). "
                f"Utilisez les flèches ◄ ► ou « Changer de période » pour naviguer."
            )
        else:
            self._sub.setText(
                f"Indicateurs pour le mois de travail <b>{ym}</b> "
                f"(situation « actuelle » en base)."
            )
