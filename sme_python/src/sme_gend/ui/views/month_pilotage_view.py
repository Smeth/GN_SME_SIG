"""Vue Pilotage du mois — alertes, cycle SME (hors synthèse statistique)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.services.etl_erreur_service import count_etl_erreurs
from sme_gend.services.pre_closure_service import run_pre_closure_checks
from sme_gend.services.sme_month_closure import (
    canonical_sme_mois_yyyy_mm,
    shift_sme_month_yyyy_mm,
)
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.bg_task import run_in_background
from sme_gend.services.month_cycle_snapshot import load_month_cycle
from sme_gend.ui.data_health_dialog import DataHealthDialog
from sme_gend.ui.navigation_pages import (
    PAGE_ARCHIVES_CLOSURE,
    PAGE_BACKUP_RESTORE,
    PAGE_PILOTAGE_MOIS,
    PAGE_SETTINGS,
)
from sme_gend.ui.views.sme_workflow_panel import SmeWorkflowPanel
from sme_gend.ui.views.workflow_status_panel import (
    WorkflowStatusPanel,
    configure_alert_navigation,
)


class MonthPilotageView(QWidget):
    """Cycle mensuel SME : alertes, workflow et historique périodes."""

    def __init__(
        self,
        settings: Settings,
        dashboard: DashboardService,
        parent=None,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
        on_rebuild_dossiers: Callable[[], None] | None = None,
        on_reconcile_carriere: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._dashboard = dashboard
        self._navigate = navigate_to_page
        self._on_rebuild_dossiers = on_rebuild_dossiers
        self._on_reconcile_carriere = on_reconcile_carriere
        self._page_scroll: QScrollArea | None = None
        self._stats_dirty = True
        self._generation, self._busy = 0, False

        configure_alert_navigation({
            "mysql_down": PAGE_SETTINGS,
            "active_mismatch": PAGE_PILOTAGE_MOIS,
            "period_locked": PAGE_PILOTAGE_MOIS,
            "period_reopen": PAGE_PILOTAGE_MOIS,
            "viewing_closed": PAGE_ARCHIVES_CLOSURE,
            "no_archive": PAGE_ARCHIVES_CLOSURE,
            "no_backup": PAGE_BACKUP_RESTORE,
            "open_tasks": PAGE_PILOTAGE_MOIS,
        })

        title = QLabel("Cycle mensuel")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Contrôlez le mois de travail avant de le clôturer. "
            "Consulter une ancienne période ne la rouvre pas et ne change pas le mois de travail."
        )
        sub.setObjectName("dashSubtitle")
        sub.setWordWrap(True)
        self._load_status = QLabel("Périodes non chargées.")
        self._load_status.setWordWrap(True)
        self._refresh = QPushButton("Actualiser le cycle")
        self._refresh.clicked.connect(lambda: self._reload(silent=True))

        self._workflow_status = WorkflowStatusPanel(
            settings, navigate_to_page=navigate_to_page
        )

        from sme_gend.ui.widgets.sot_status_banner import SotStatusBanner

        self._sot_banner = SotStatusBanner(
            settings,
            self,
            on_resync_done=lambda: self._reload(silent=True),
        )

        maint_bar = QHBoxLayout()
        maint_bar.setSpacing(8)
        self._btn_data_health = QPushButton("Santé des données…")
        self._btn_data_health.clicked.connect(self._open_data_health)
        self._btn_preclose = QPushButton("Contrôler avant clôture…")
        self._btn_preclose.setToolTip(
            "Lance les mêmes contrôles que l'assistant de clôture (sans fermer le mois)."
        )
        self._btn_preclose.clicked.connect(self._run_preclose_preview)
        self._btn_rebuild = QPushButton("Reconstruire dossiers…")
        self._btn_rebuild.clicked.connect(self._trigger_rebuild_dossiers)
        self._btn_reconcile = QPushButton("Réconcilier carrière…")
        self._btn_reconcile.clicked.connect(self._trigger_reconcile)
        maint_bar.addWidget(self._btn_data_health)
        maint_bar.addWidget(self._btn_rebuild)
        maint_bar.addWidget(self._btn_reconcile)
        maint_bar.addStretch()
        self._data_health_loaded = False

        self._sme_panel = SmeWorkflowPanel(
            settings,
            navigate_to_page=navigate_to_page,
            on_after_workflow=lambda: self._reload(silent=True),
        )

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(20)
        inner_lay.addWidget(title)
        inner_lay.addWidget(sub)
        inner_lay.addWidget(self._sot_banner)
        head = QHBoxLayout()
        head.addWidget(self._load_status, 1)
        head.addWidget(self._btn_preclose)
        head.addWidget(self._refresh)
        inner_lay.addLayout(head)
        inner_lay.addWidget(self._sme_panel)
        inner_lay.addLayout(maint_bar)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self._page_scroll = scroll

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

        # Chargement MySQL uniquement à l'ouverture de la page (pas au démarrage de l'app).

    def mark_dirty(self) -> None:
        self._generation += 1
        self._busy = False
        self._refresh.setEnabled(True)
        self._stats_dirty = True
        self._workflow_status.mark_dirty()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        from sme_gend.services.auth_guard import session_has_permission
        from sme_gend.services.workflow_permissions import session_can_perform_action
        if not session_has_permission("workflow.pilotage"):
            return
        self._btn_rebuild.setEnabled(bool(self._on_rebuild_dossiers) and session_can_perform_action("workflow.import_archive"))
        self._btn_reconcile.setEnabled(bool(self._on_reconcile_carriere) and session_can_perform_action("workflow.import_archive"))
        if self._stats_dirty:
            self._reload(silent=True)

    def open_closure_dialog(self, parent=None) -> None:
        """Raccourci vers l'assistant de clôture (sidebar ou page Pilotage)."""
        self._sme_panel.open_closure_dialog(parent or self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Rechargement via on_page_activated() (navigation) — évite double chargement MySQL au démarrage.

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.mark_dirty()
        self._workflow_status.apply_runtime_settings(settings)
        self._sme_panel.apply_runtime_settings(settings)
        self._sot_banner.apply_settings(settings)
        self._stats_dirty = True
        if self.isVisible():
            self._reload(silent=True)

    def _reload(self, *, silent: bool = False) -> None:
        from sme_gend.services.auth_guard import session_has_permission
        if self._busy or not session_has_permission("workflow.pilotage"):
            return
        self._generation += 1
        generation, settings = self._generation, self._settings
        self._busy = True
        self._refresh.setEnabled(False)
        self._load_status.setText("Chargement des périodes et des contrôles…")
        def work():
            snapshot = load_month_cycle(settings)
            try:
                health = count_etl_erreurs(settings)
            except Exception:
                health = -1
            from sme_gend.services.sot_status_service import fetch_sot_status_summary
            try:
                sot = fetch_sot_status_summary(settings)
            except Exception:
                sot = None
            return snapshot, health, sot
        def loaded(result):
            if generation != self._generation:
                return
            self._busy, self._stats_dirty = False, False
            self._refresh.setEnabled(True)
            if not session_has_permission("workflow.pilotage"):
                self._sme_panel._period_rows = []
                self._sme_panel._fill_periods()
                self._load_status.setText("Consultation non autorisée.")
                return
            snapshot, health, sot = result
            self._sme_panel.update_sme_table(snapshot)
            self._load_status.setText(f"Mois de travail : {snapshot.active_month or 'non défini'} · {len(snapshot.sme_rows)} période(s) enregistrée(s).")
            if health < 0 or sot is None:
                self._load_status.setText(self._load_status.text() + " Certains contrôles complémentaires sont indisponibles.")
            self._btn_data_health.setText("Santé des données indisponible" if health < 0 else f"Santé des données · {health} erreur(s)")
            self._sot_banner.update_summary(sot)
        def failed(error):
            if generation == self._generation:
                self._busy = False
                self._sme_panel._period_rows = []
                self._sme_panel._fill_periods()
                self._refresh.setEnabled(True)
                self._load_status.setText("Chargement impossible. Les périodes ne sont pas actualisées ; vérifiez la connexion puis réessayez.")
                self._stats_dirty = True
        run_in_background(self, work, loaded, on_error=failed)

    def _refresh_sot_banner(self) -> None:
        from sme_gend.services.sot_status_service import fetch_sot_status_summary

        try:
            summary = fetch_sot_status_summary(self._settings)
        except Exception:
            summary = None
        self._sot_banner.apply_settings(self._settings)
        self._sot_banner.update_summary(summary)

    def _refresh_data_health_label(self) -> None:
        from sme_gend.ui.theme import repolish_widget

        try:
            n = count_etl_erreurs(self._settings)
        except Exception:
            n = -1
        if n < 0:
            txt = "Santé des données (indisponible)"
            state = None
        elif n == 0:
            txt = "Santé des données (OK)"
            state = "ok"
        else:
            txt = f"Santé des données ({n} erreur(s))"
            state = "error"
        self._btn_data_health.setText(txt)
        self._btn_data_health.setProperty("healthState", state)
        repolish_widget(self._btn_data_health)

    def _open_data_health(self) -> None:
        DataHealthDialog(self._settings, self).exec()
        self._refresh_data_health_label()

    def _run_preclose_preview(self) -> None:
        from sme_gend.services.auth_guard import session_has_permission
        if not session_has_permission("workflow.pilotage"):
            return
        settings = self._settings
        def work():
            from sme_gend.services.sme_workspace_service import SmeWorkspaceService
            month = SmeWorkspaceService(settings).load_active_month_snapshot().active_month
            mois = canonical_sme_mois_yyyy_mm(month or "") or ""
            suivant = shift_sme_month_yyyy_mm(mois, delta_months=1) or ""
            if not mois or not suivant:
                raise ValueError("Définissez un mois de travail unique avant de lancer les contrôles.")
            return run_pre_closure_checks(settings, mois, suivant)
        report = run_with_progress(self, "Contrôles avant clôture", "Vérification du mois et des données…",
                                   work)
        if report is None or settings != self._settings or not session_has_permission("workflow.pilotage"):
            return
        from sme_gend.ui.pre_closure_report_dialog import PreClosureReportDialog
        PreClosureReportDialog(self, report, settings).exec()

    def _trigger_rebuild_dossiers(self) -> None:
        from sme_gend.services.workflow_permissions import session_can_perform_action
        if not session_can_perform_action("workflow.import_archive"):
            return
        if self._on_rebuild_dossiers:
            self._on_rebuild_dossiers()

    def _trigger_reconcile(self) -> None:
        from sme_gend.services.workflow_permissions import session_can_perform_action
        if not session_can_perform_action("workflow.import_archive"):
            return
        if self._on_reconcile_carriere:
            self._on_reconcile_carriere()
