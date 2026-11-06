"""Fenêtre principale — menu, sidebar brandée, navigation arborescente."""

from __future__ import annotations

from sme_gend.config.ui_labels import IMPORT_MONTH_ACCESS_ACTION

import logging

from PySide6.QtCore import QTimer, QSize, Qt, QEvent
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend import __version__
from sme_gend.config.branding import APP_NAME, APP_NAME_FULL, APP_SUBTITLE, SME_FULL, WINDOW_TITLE
from sme_gend.config import get_settings
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context, load_local_sme_context
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_preview_service import DetachesPreviewService
from sme_gend.services.numerique_preview_service import NumeriquePreviewService
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.resources import app_icon_path, load_app_icon
from sme_gend.ui.views.detaches_config_view import DetachesConfigView
from sme_gend.ui.views.detaches_view import DetachesView
from sme_gend.ui.views.home_view import HomeView
from sme_gend.ui.views.month_pilotage_view import MonthPilotageView
from sme_gend.ui.views.layout_editor_view import LayoutEditorView
from sme_gend.ui.views.liste_present_view import ListePresentView
from sme_gend.ui.views.sme_dpf_view import SmeDpfView
from sme_gend.ui.views.numerique_combined_view import NumeriqueCombinedView
from sme_gend.ui.views.personnel_list_view import PersonnelListView
from sme_gend.ui.views.distinction_referentiel_view import DistinctionsView
from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, PadminView, StructuresView
from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView
from sme_gend.ui.navigation_pages import (
    PAGE_COUNT,
    PAGE_DASHBOARD,
    PAGE_ARCHIVES_CLOSURE,
    PAGE_DETACHES,
    PAGE_DETACHES_CONFIG,
    PAGE_DOC_GEN_LOG,
    PAGE_SENSITIVE_AUDIT,
    PAGE_SME_DPF,
    PAGE_STAGES,
    PAGE_MISSIONS,
    PAGE_DESERTION,
    PAGE_CONGES,
    PAGE_EDITION_SME,
    PAGE_FONCTIONS,
    PAGE_PADMIN,
    PAGE_GAIN_PERTE,
    PAGE_GRADES,
    PAGE_MALADE_PUNIS,
    PAGE_LAYOUT_EDITOR,
    PAGE_LIMITE_AGE,
    PAGE_LISTE_GENERALE,
    PAGE_LISTE_PRESENTS,
    PAGE_NUMERIQUE,
    PAGE_SETTINGS,
    PAGE_SOIT_TRANSMIS,
    PAGE_STRUCTURES,
    PAGE_STRUCTURES_EFFECTIFS,
    PAGE_BACKUP_RESTORE,
    PAGE_PILOTAGE_MOIS,
    PAGE_USERS_AUTH,
    PAGE_AVANCEMENT,
    PAGE_DELETED_PERSONNEL,
    is_avancement_page,
)
from sme_gend.ui.views.backup_restore_view import BackupRestoreView
from sme_gend.ui.views.archives_view import ArchivesView
from sme_gend.ui.views.cycle_archives_view import CycleArchivesView
from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView
from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
from sme_gend.ui.views.edition_sme_view import EditionSmeView
from sme_gend.ui.views.gain_perte_view import GainPerteView
from sme_gend.ui.views.malade_punis_view import MaladePunisView
from sme_gend.ui.views.stages_view import StagesView
from sme_gend.ui.views.missions_view import MissionsView
from sme_gend.ui.views.desertion_view import DesertionView
from sme_gend.ui.views.conges_view import CongesView
from sme_gend.ui.views.avancement_view import AvancementView
from sme_gend.ui.views.deleted_personnel_view import DeletedPersonnelView
from sme_gend.ui.views.limite_age_view import LimiteAgeView
from sme_gend.ui.views.settings_view import SettingsView
from sme_gend.ui.views.soit_transmis_view import SoitTransmisView
from sme_gend.ui.views.users_auth_view import UsersAuthView

from sme_gend.ui.context_banner import ContextBanner
from sme_gend.ui.access_control import first_accessible_page, session_can_access_page
from sme_gend.ui.session_lock_overlay import SessionLockOverlay
from sme_gend.ui.lazy_stack_page import LazyStackPage
from sme_gend.ui.sidebar_navigation import (
    ROLE_KIND,
    ROLE_LABEL,
    ROLE_STACK,
    build_sidebar_nav,
    default_nav_groups,
    page_nav_icons,
)

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


def _format_mois_fr(mois: str) -> str:
    if not mois:
        return "—"
    try:
        y, m = mois.split("-")
        m_int = int(m)
        if 1 <= m_int <= 12:
            return f"{_MOIS_FR[m_int - 1]} {y}"
    except (ValueError, IndexError):
        pass
    return mois


_PERIOD_SOURCE_STYLE_BASE = "color: #c8e6c9; font-size: 11px;"
_PERIOD_SOURCE_STYLE_ARCHIVE = "color: #ffcc80; font-size: 11px; font-weight: 600;"
_PERIOD_SOURCE_STYLE_NEUTRAL = "color: #b0bec5; font-size: 11px;"
_LOG = logging.getLogger(__name__)
_MAX_PAGE_NAV_HISTORY = 50


class _PlaceholderView(QWidget):
    """Page vide affichée pour les modules en cours de développement."""

    def __init__(self, title: str, message: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lbl_title = QLabel(title)
        lbl_title.setObjectName("dashTitle")
        lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_msg = QLabel(message)
        lbl_msg.setObjectName("dashSubtitle")
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_msg.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addStretch()
        lay.addWidget(lbl_title)
        lay.addSpacing(12)
        lay.addWidget(lbl_msg)
        lay.addStretch()


class ShellWindow(QMainWindow):
    def __init__(
        self,
        settings: Settings,
        personnel: PersonnelService,
        referentiel: ReferentielService,
        liste_present: ListePresentService,
        layout_cfg: LayoutConfigService,
        dashboard: DashboardService,
        numerique_preview: NumeriquePreviewService,
        detaches_preview: DetachesPreviewService,
        detaches_cfg: DetachesConfigService,
        *,
        auth_session=None,
    ) -> None:
        super().__init__()
        self._auth_session = auth_session
        self._personnel = personnel
        self._settings = settings
        self._referentiel = referentiel
        self._liste_present = liste_present
        self._layout_cfg = layout_cfg
        self._dashboard = dashboard
        self._numerique_preview = numerique_preview
        self._detaches_preview = detaches_preview
        self._detaches_cfg = detaches_cfg

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1400, 820)
        self.setMinimumSize(1024, 680)

        style = self.style()
        self._page_to_item: dict[int, QTreeWidgetItem] = {}
        self._nav_menu_actions: dict[int, QAction] = {}
        self._nav_group_menus: list = []
        self._nav_labels: list[str] = []
        self._sidebar_compact = False

        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")

        self._attestation_wired = False
        self._liste_layout_wired = False
        self._last_nav_index: int = PAGE_DASHBOARD
        self._chrome_nav_index: int = -1
        self._nav_back_stack: list[int] = []
        self._nav_forward_stack: list[int] = []
        self._chrome_editing_busy = False
        self._prewarm_queue: list[int] = []
        self._personnel_list_view: PersonnelListView | None = None
        self._edition_sme_view: EditionSmeView | None = None
        self._sme_dpf_view: SmeDpfView | None = None
        self._pending_liste_structure_filter: str | None = None
        self._pending_liste_search: str | None = None
        self._pending_fonctions_structure: str | None = None
        self._fonctions_view: FonctionsView | None = None
        self._pending_gain_perte_mois: str | None = None
        self._pending_gain_perte_filter: str | None = None
        self._pending_avancement_lookup: str | None = None
        self._avancement_view: AvancementView | None = None
        self._backup_restore_view: BackupRestoreView | None = None
        self._limite_age_view: LimiteAgeView | None = None
        self._soit_transmis_view: SoitTransmisView | None = None

        def _add_page(label: str, widget: QWidget) -> None:
            self._stack.addWidget(widget)
            self._nav_labels.append(label)

        def _add_lazy(label: str, factory) -> None:
            # Toujours différé : SME_FULL_FEATURES ne doit pas bloquer l'ouverture après connexion.
            self._stack.addWidget(LazyStackPage(factory))
            self._nav_labels.append(label)

        def _open_liste_for_structure(structure_code: str) -> None:
            self._pending_liste_structure_filter = (structure_code or "").strip() or None
            self._go_page(PAGE_LISTE_GENERALE)
            pl = self._personnel_list_view
            if pl is not None:
                pl.apply_structure_filter(structure_code)
                self._pending_liste_structure_filter = None

        def _open_fonctions_for_structure(structure_code: str) -> None:
            self._pending_fonctions_structure = (structure_code or "").strip() or None
            self._go_page(PAGE_FONCTIONS)
            fv = self._fonctions_view
            if fv is not None and self._pending_fonctions_structure:
                fv.focus_structure(self._pending_fonctions_structure)
                self._pending_fonctions_structure = None

        # --- Pages (ordre = navigation_pages ; chargement différé hors accueil) ---
        self._home_view = HomeView(
            settings,
            dashboard,
            navigate_to_page=self._go_page,
            navigate_gain_perte=self._go_gain_perte,
        )
        _add_page("Tableau de bord", self._home_view)

        self._pilotage_view = MonthPilotageView(
            settings,
            dashboard,
            navigate_to_page=self._go_page,
            on_rebuild_dossiers=self._on_rebuild_dossiers_menu,
            on_reconcile_carriere=self._on_reconcile_carriere_menu,
        )
        self._archives_view = ArchivesView()
        self._cycle_archives_view = CycleArchivesView(
            self._pilotage_view, self._archives_view
        )

        def _fab_personnel() -> PersonnelListView:
            v = PersonnelListView(personnel, referentiel, self._personnel.settings)
            self._personnel_list_view = v
            return v

        _add_lazy("Liste générale", _fab_personnel)
        _add_lazy(
            "Effectifs par structure",
            lambda: StructureEffectifsView(
                self._personnel.settings, personnel, referentiel, _open_liste_for_structure
            ),
        )
        _add_lazy(
            "Mise en page (liste présents)",
            lambda: LayoutEditorView(layout_cfg, detaches_cfg),
        )
        _add_lazy(
            "Liste des présents",
            lambda: ListePresentView(liste_present, layout_cfg),
        )
        _add_lazy(
            "Détachés",
            lambda: DetachesView(self._personnel.settings, detaches_preview, detaches_cfg),
        )
        _add_lazy(
            "Structures & subdivisions",
            lambda: StructuresView(
                referentiel, navigate_to_fonctions=_open_fonctions_for_structure
            ),
        )
        _add_lazy("Grades", lambda: GradesView(referentiel))

        def _fab_fonctions() -> FonctionsView:
            v = FonctionsView(referentiel)
            self._fonctions_view = v
            return v

        _add_lazy("Fonctions", _fab_fonctions)
        _add_lazy(
            "Fichier Excel détachés",
            lambda: DetachesConfigView(detaches_cfg),
        )
        _add_lazy(
            "Situation numérique",
            lambda: NumeriqueCombinedView(self._personnel.settings, numerique_preview),
        )
        _add_page("Gestion des mois et archives", self._cycle_archives_view)
        _add_lazy("Paramètres & données", lambda: SettingsView(self._settings))

        def _fab_backup() -> BackupRestoreView:
            v = BackupRestoreView(self._settings)
            self._backup_restore_view = v
            return v

        _add_lazy("Sauvegarde & restauration", _fab_backup)
        _add_lazy("Gains & Pertes", lambda: GainPerteView(self._personnel.settings))
        _add_lazy("Malades & Punis", lambda: MaladePunisView(self._personnel.settings))

        def _fab_limite() -> LimiteAgeView:
            v = LimiteAgeView(self._personnel.settings)
            self._limite_age_view = v
            self._ensure_attestation_wiring()
            return v

        _add_lazy("Attestations et suivi de la retraite", _fab_limite)

        def _fab_soit() -> SoitTransmisView:
            v = SoitTransmisView(self._personnel.settings)
            self._soit_transmis_view = v
            self._ensure_attestation_wiring()
            return v

        _add_lazy("Soit transmis", _fab_soit)

        # Reserved index 18: retired personnel-list importer. Keep later page IDs stable.
        _add_page("", QWidget())
        _add_lazy("Historique documents", DocGenLogView)

        def _fab_edition_sme() -> EditionSmeView:
            v = EditionSmeView(self._personnel.settings, navigate_to_page=self._go_page)
            self._edition_sme_view = v
            return v

        _add_lazy("Édition SME", _fab_edition_sme)
        _add_lazy("Positions administratives", lambda: PadminView(referentiel))
        _add_lazy("Utilisateurs & droits", UsersAuthView)
        _add_page("Cycle mensuel", QWidget())  # alias virtuel → PAGE_ARCHIVES_CLOSURE
        _add_lazy("Audit données sensibles", SensitiveAuditLogView)

        def _fab_sme_dpf() -> SmeDpfView:
            v = SmeDpfView(self._personnel.settings, navigate_to_page=self._go_page)
            self._sme_dpf_view = v
            return v

        _add_lazy("SME DPF", _fab_sme_dpf)
        _add_lazy("Stages", lambda: StagesView(self._personnel.settings))
        _add_lazy("Missions", lambda: MissionsView(self._personnel.settings))
        _add_lazy("Désertions", lambda: DesertionView(self._personnel.settings))
        _add_lazy("Congés", lambda: CongesView(self._personnel.settings))
        _add_lazy("Médailles & décorations", lambda: DistinctionsView(referentiel))

        def _fab_avancement() -> AvancementView:
            v = AvancementView(self._personnel.settings)
            self._avancement_view = v
            return v

        _add_lazy("Avancement au grade", _fab_avancement)
        _add_lazy(
            "Fiches retirées de la liste",
            lambda: DeletedPersonnelView(self._settings),
        )

        from sme_gend.ui.views.career_alerts_view import CareerAlertsView
        _add_lazy("Alertes parcours", lambda: CareerAlertsView(self._personnel.settings, personnel, referentiel))

        _add_lazy("Journal d’activité global", ActivityAuditLogView)

        if self._stack.count() != PAGE_COUNT:
            raise RuntimeError(
                f"navigation_pages.PAGE_COUNT={PAGE_COUNT} != pile={self._stack.count()} "
                "(mettre à jour navigation_pages si l'ordre des pages change)."
            )

        icons: list[QIcon] = page_nav_icons(style)
        if len(icons) != PAGE_COUNT:
            raise RuntimeError(
                f"Incohérence icônes/pages : {len(icons)} icônes pour PAGE_COUNT={PAGE_COUNT}. "
                "Mettre à jour la liste icons[] lors de tout ajout de page."
            )

        self._nav = QTreeWidget()
        self._nav.setObjectName("navTree")
        self._nav.setHeaderHidden(True)
        self._nav.setRootIsDecorated(True)
        self._nav.setIndentation(12)
        self._nav.setAnimated(True)
        self._nav.setIconSize(QSize(20, 20))
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setUniformRowHeights(True)
        self._nav.setWordWrap(False)
        self._nav.setTextElideMode(Qt.TextElideMode.ElideRight)

        build_sidebar_nav(
            self._nav,
            style=style,
            nav_labels=self._nav_labels,
            page_icons=icons,
            page_to_item=self._page_to_item,
        )
        self._apply_nav_permissions()

        self._nav.itemClicked.connect(self._on_nav_item_clicked)
        self._stack.setCurrentIndex(PAGE_DASHBOARD)
        self._stack.currentChanged.connect(self._on_content_page_changed)
        self._update_window_page_title(self._stack.currentIndex())

        # Ctrl+K — recherche globale
        self._shortcut_search = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_search.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_search.activated.connect(self._open_global_search)
        self._global_search_dlg = None

        # F1 / aide : QShortcut plus fiable que QAction seul sous Windows (conflits menu / focus)
        self._shortcut_about = QShortcut(QKeySequence("F1"), self)
        self._shortcut_about.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_about.activated.connect(self._about)
        self._shortcut_about_ctrl = QShortcut(QKeySequence("Ctrl+H"), self)
        self._shortcut_about_ctrl.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_about_ctrl.activated.connect(self._about)

        # --- En-tête étendu (logo + textes + bouton réduire) ---
        logo_path = app_icon_path()
        self._logo_lbl = QLabel()
        if logo_path is not None:
            self._logo_lbl.setPixmap(QIcon(str(logo_path)).pixmap(52, 52))
        self._logo_lbl.setFixedSize(56, 56)

        self._sidebar_brand = QLabel("GENDARMERIE NATIONALE")
        self._sidebar_brand.setObjectName("sidebarBrand")
        self._sidebar_title = QLabel(APP_NAME)
        self._sidebar_title.setObjectName("sidebarTitle")
        self._sidebar_sub = QLabel(APP_SUBTITLE)
        self._sidebar_sub.setObjectName("sidebarSubtitle")
        if auth_session is not None:
            self._sidebar_sub.setText(auth_session.display_name or auth_session.username)

        head_txt = QVBoxLayout()
        head_txt.setSpacing(2)
        head_txt.addWidget(self._sidebar_brand)
        head_txt.addWidget(self._sidebar_title)
        head_txt.addWidget(self._sidebar_sub)

        self._btn_collapse_sidebar = QToolButton()
        self._btn_collapse_sidebar.setText("◀")
        self._btn_collapse_sidebar.setObjectName("sidebarCollapseBtn")
        self._btn_collapse_sidebar.setToolTip("Réduire aux icônes")
        self._btn_collapse_sidebar.setAutoRaise(True)
        self._btn_collapse_sidebar.setMinimumSize(36, 36)
        self._btn_collapse_sidebar.clicked.connect(self._collapse_sidebar)

        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        head_row.addWidget(self._logo_lbl, alignment=Qt.AlignmentFlag.AlignTop)
        head_row.addLayout(head_txt, stretch=1)
        head_row.addWidget(self._btn_collapse_sidebar, alignment=Qt.AlignmentFlag.AlignTop)

        self._head_expanded = QWidget()
        self._head_expanded.setObjectName("sidebarHeader")
        self._head_expanded.setLayout(head_row)
        self._head_expanded.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # --- En-tête compact : uniquement un bouton large « développer » (évite logo 56px + barre 72px) ---
        self._btn_expand_sidebar = QToolButton()
        self._btn_expand_sidebar.setText("▶")
        self._btn_expand_sidebar.setObjectName("sidebarExpandBtn")
        self._btn_expand_sidebar.setToolTip("Développer la barre latérale")
        self._btn_expand_sidebar.setMinimumHeight(40)
        self._btn_expand_sidebar.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._btn_expand_sidebar.clicked.connect(self._expand_sidebar)

        self._logo_small = QLabel()
        if logo_path is not None:
            self._logo_small.setPixmap(QIcon(str(logo_path)).pixmap(28, 28))
        self._logo_small.setFixedSize(32, 32)
        self._logo_small.setAlignment(Qt.AlignmentFlag.AlignCenter)

        head_narrow_lay = QVBoxLayout()
        head_narrow_lay.setSpacing(8)
        head_narrow_lay.setContentsMargins(4, 8, 4, 8)
        head_narrow_lay.addWidget(self._btn_expand_sidebar)
        head_narrow_lay.addWidget(self._logo_small, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._head_compact = QWidget()
        self._head_compact.setObjectName("sidebarHeaderCompact")
        self._head_compact.setLayout(head_narrow_lay)
        self._head_compact.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._head_compact.hide()

        self._header_stack = QStackedWidget()
        self._header_stack.setObjectName("sidebarHeaderStack")
        self._header_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._header_stack.addWidget(self._head_expanded)
        self._header_stack.addWidget(self._head_compact)

        self._sidebar_frame = QFrame()
        self._sidebar_frame.setObjectName("sidebarFrame")
        self._sidebar_frame.setMinimumWidth(248)
        self._sidebar_frame.setMaximumWidth(288)
        self._side_lay = QVBoxLayout(self._sidebar_frame)
        self._side_lay.setContentsMargins(12, 20, 12, 16)
        self._side_lay.setSpacing(8)
        self._side_lay.addWidget(self._header_stack)
        self._side_lay.addWidget(self._nav, stretch=1)

        # --- Pied de sidebar : période SME + raccourcis ---
        self._period_panel = QFrame()
        self._period_panel.setObjectName("sidebarPeriod")
        period_lay = QVBoxLayout(self._period_panel)
        period_lay.setContentsMargins(10, 10, 10, 8)
        period_lay.setSpacing(6)

        lbl_period_title = QLabel("Période de travail")
        lbl_period_title.setObjectName("sidebarPeriodTitle")
        period_lay.addWidget(lbl_period_title)

        month_row = QHBoxLayout()
        month_row.setSpacing(4)
        self._btn_prev_month = QToolButton()
        self._btn_prev_month.setText("◄")
        self._btn_prev_month.setObjectName("sidebarPeriodNavBtn")
        self._btn_prev_month.setToolTip(
            "Mois précédent (consultation si clôturé, sans modifier sme_periode)"
        )
        self._btn_prev_month.setAutoRaise(True)
        self._btn_prev_month.clicked.connect(self._go_prev_month)

        self._period_month_lbl = QLabel("—")
        self._period_month_lbl.setObjectName("sidebarPeriodMonth")
        self._period_month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._period_month_lbl.setWordWrap(True)

        self._btn_next_month = QToolButton()
        self._btn_next_month.setText("►")
        self._btn_next_month.setObjectName("sidebarPeriodNavBtn")
        self._btn_next_month.setToolTip(
            "Mois suivant (consultation si clôturé, sans modifier sme_periode)"
        )
        self._btn_next_month.setAutoRaise(True)
        self._btn_next_month.clicked.connect(self._go_next_month)

        month_row.addWidget(self._btn_prev_month)
        month_row.addWidget(self._period_month_lbl, stretch=1)
        month_row.addWidget(self._btn_next_month)
        period_lay.addLayout(month_row)

        self._period_statut_lbl = QLabel()
        self._period_statut_lbl.setObjectName("sidebarPeriodStatut")
        self._period_statut_lbl.setWordWrap(True)
        period_lay.addWidget(self._period_statut_lbl)

        self._period_source_lbl = QLabel()
        self._period_source_lbl.setObjectName("sidebarPeriodSource")
        self._period_source_lbl.setWordWrap(True)
        period_lay.addWidget(self._period_source_lbl)

        _CLOSE_MONTH_TTIP = (
            "Clôturer le mois actif, archiver la mise en page et activer le mois suivant "
            "(mise à jour sme_periode en base).\n\n"
            "Ce n'est pas la navigation ◄ ► : les flèches changent seulement le mois consulté, "
            "sans clôture ni archive."
        )
        self._btn_close_month = QPushButton("Clôturer / nouveau mois…")
        self._btn_close_month.setObjectName("sidebarPeriodBtnPrimary")
        self._btn_close_month.setToolTip(_CLOSE_MONTH_TTIP)
        self._btn_close_month.clicked.connect(self._open_month_closure_from_sidebar)
        period_lay.addWidget(self._btn_close_month)

        btn_months_overview = QPushButton("Changer de période…")
        btn_months_overview.setObjectName("sidebarPeriodBtn")
        btn_months_overview.setToolTip(
            "Calendrier des mois : statut, archive disque, données MySQL. "
            "Double-clic = consulter un mois clôturé sans le rouvrir en base ; "
            "mois ouvert = devient le mois de travail."
        )
        btn_months_overview.clicked.connect(self._on_months_overview_dialog)
        period_lay.addWidget(btn_months_overview)

        btn_period = QPushButton("Pilotage SME…")
        btn_period.setObjectName("sidebarPeriodBtn")
        btn_period.setToolTip("Alertes, clôture et réouverture.")
        btn_period.clicked.connect(lambda: self._go_page(PAGE_PILOTAGE_MOIS))
        period_lay.addWidget(btn_period)

        self._side_lay.addWidget(self._period_panel)

        self._btn_close_month_compact = QToolButton()
        self._btn_close_month_compact.setText("Clôturer")
        self._btn_close_month_compact.setObjectName("sidebarPeriodBtnPrimary")
        self._btn_close_month_compact.setToolTip(_CLOSE_MONTH_TTIP)
        self._btn_close_month_compact.setAutoRaise(True)
        self._btn_close_month_compact.clicked.connect(self._open_month_closure_from_sidebar)
        self._btn_close_month_compact.hide()
        self._side_lay.addWidget(self._btn_close_month_compact)

        self._btn_search_sidebar = QToolButton()
        self._btn_search_sidebar.setObjectName("sidebarFooterBtn")
        self._btn_search_sidebar.setText("Recherche")
        self._btn_search_sidebar.setToolTip("Recherche personnel (Ctrl+K)")
        self._btn_search_sidebar.setAutoRaise(True)
        self._btn_search_sidebar.clicked.connect(self._open_global_search)

        self._btn_logout_sidebar = QToolButton()
        self._btn_logout_sidebar.setObjectName("sidebarFooterBtn")
        self._btn_logout_sidebar.setText("Se déconnecter")
        self._btn_logout_sidebar.setToolTip("Fermer la session et revenir à l'écran de connexion.")
        self._btn_logout_sidebar.setAutoRaise(True)
        self._btn_logout_sidebar.setEnabled(self._auth_session is not None)
        self._btn_logout_sidebar.clicked.connect(self._logout_user)
        footer_actions = QHBoxLayout()
        footer_actions.setContentsMargins(0, 0, 0, 0)
        footer_actions.setSpacing(12)
        footer_actions.addWidget(self._btn_logout_sidebar)
        footer_actions.addWidget(self._btn_search_sidebar)
        footer_actions.addStretch()
        self._side_lay.addLayout(footer_actions)

        self._context_banner = ContextBanner(settings)
        self._context_banner.set_configure_mysql_callback(self._open_paths_config)
        self._context_banner.set_mysql_status(True, "connecté")
        self._context_banner.set_page_nav_handlers(self._nav_go_back, self._nav_go_forward)
        self._mysql_online = True

        content_col = QWidget()
        col_lay = QVBoxLayout(content_col)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(0)
        col_lay.addWidget(self._context_banner)
        col_lay.addWidget(self._stack, stretch=1)

        content_row = QWidget()
        row_lay = QHBoxLayout(content_row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(0)
        row_lay.addWidget(self._sidebar_frame)
        row_lay.addWidget(content_col, stretch=1)
        self.setCentralWidget(content_row)

        self._shortcut_reload = QShortcut(QKeySequence("F5"), self)
        self._shortcut_reload.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_reload.activated.connect(self._refresh_shared_data_now)
        self._shortcut_filter = QShortcut(QKeySequence("Ctrl+F"), self)
        self._shortcut_filter.activated.connect(self._focus_current_filter)

        self._shortcut_nav_back = QShortcut(QKeySequence("Alt+Left"), self)
        self._shortcut_nav_back.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_nav_back.activated.connect(self._nav_go_back)
        self._shortcut_nav_forward = QShortcut(QKeySequence("Alt+Right"), self)
        self._shortcut_nav_forward.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut_nav_forward.activated.connect(self._nav_go_forward)

        self._consultation_mois: str | None = None
        self._last_operational_mois: str = ""
        self._home_bootstrapping = True
        # Rebind différé : évite de construire des pages lourdes avant l'affichage de la fenêtre
        QTimer.singleShot(0, self._finish_startup_refresh)
        self._build_menu_bar()

        status = QStatusBar()
        self.setStatusBar(status)
        status.showMessage("Prêt")
        self._status_data_lbl = QLabel()
        self._refresh_data_status_label()
        self._status_data_lbl.setMinimumWidth(120)
        self._status_data_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        status.addPermanentWidget(self._status_data_lbl, 1)
        self._sync_status_label = QLabel("Synchronisation : premier contrôle en attente")
        self._sync_status_label.setMaximumWidth(340)
        self._sync_retry = QPushButton("Actualiser")
        self._sync_retry.setToolTip("Charger les dernières modifications à votre demande (F5)")
        self._sync_retry.clicked.connect(self._refresh_shared_data_now)
        status.addPermanentWidget(self._sync_status_label)
        status.addPermanentWidget(self._sync_retry)

        self._chrome_refresh_timer = QTimer(self)
        self._chrome_refresh_timer.setSingleShot(True)
        self._chrome_refresh_timer.setInterval(200)
        self._chrome_refresh_timer.timeout.connect(self._refresh_chrome_after_nav)

        self._mysql_health_timer = QTimer(self)
        self._mysql_health_timer.setInterval(45_000)
        self._mysql_health_timer.timeout.connect(self._check_mysql_health)
        self._mysql_health_timer.start()
        QTimer.singleShot(3_000, self._check_mysql_health)
        self._shared_revision = None
        self._shared_revision_pending = None
        self._shared_revision_busy = False
        self._shared_revision_timer = QTimer(self)
        self._shared_revision_timer.setInterval(30_000)
        self._shared_revision_timer.timeout.connect(self._poll_shared_data_revision)
        self._shared_revision_timer.start()
        QTimer.singleShot(1_000, self._poll_shared_data_revision)

        self._journal_recovery_busy = False
        self._journal_recovery_timer = QTimer(self)
        self._journal_recovery_timer.setInterval(45_000)
        self._journal_recovery_timer.timeout.connect(self._recover_local_journals)
        self._journal_recovery_timer.start()
        QTimer.singleShot(2_000, self._recover_local_journals)

        self._session_lock_active = False
        self._session_verification_pending = False
        self._locked_username = ""
        self._session_lock_overlay = SessionLockOverlay(self)
        self._session_lock_overlay.sync_geometry()
        self._install_session_guard()

    _SESSION_ACTIVITY = frozenset({
        QEvent.Type.KeyPress,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
    })

    def _install_session_guard(self) -> None:
        """Surveille l'expiration de session (timer + activité utilisateur)."""
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is None or not auth.is_enforced() or self._auth_session is None:
            return
        self._session_guard_timer = QTimer(self)
        self._session_guard_timer.setInterval(15_000)
        self._session_guard_timer.timeout.connect(self._check_session_expiry)
        self._session_guard_timer.start()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if getattr(self, '_session_verification_pending', False) and event.type() in (
            self._SESSION_ACTIVITY | {QEvent.Type.KeyRelease, QEvent.Type.MouseButtonDblClick,
                                      QEvent.Type.Shortcut, QEvent.Type.ShortcutOverride,
                                      QEvent.Type.TouchUpdate, QEvent.Type.TouchEnd}
        ):
            return True
        if (
            not self._session_lock_active
            and event.type() in self._SESSION_ACTIVITY
            and self.isVisible()
        ):
            from sme_gend.services.auth_context import get_app_auth

            auth = get_app_auth()
            if auth is not None and auth.is_enforced():
                active_session = auth.record_activity()
                if active_session is not None:
                    self._auth_session = active_session
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_session_lock_overlay"):
            self._session_lock_overlay.sync_geometry()

    def _check_session_expiry(self) -> None:
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is None or not auth.is_enforced() or self._session_lock_active:
            return
        if self._auth_session is None:
            return
        try:
            sync = auth.sync_session_from_config()
        except Exception:
            self._suspend_session_verification()
            return
        if sync == "invalid":
            self._session_verification_pending = False
            self._lock_session(
                detail=(
                    "Reconnectez-vous pour continuer.\n"
                    "Les données à l'écran restent masquées tant que la session n'est pas rétablie.\n\n"
                    "Cause possible : expiration, déconnexion depuis un autre poste, "
                    "ou clôture par un administrateur."
                ),
            )
            return
        if getattr(self, '_session_verification_pending', False):
            self._session_verification_pending = False
            self._session_lock_overlay.hide()
            self._auth_session = auth.current_session()
            self.statusBar().showMessage("Connexion rétablie — session vérifiée.", 5000)
        if sync == "updated":
            self._auth_session = auth.current_session()
            self._apply_nav_permissions()
            cur = self._stack.currentIndex()
            if not session_can_access_page(cur):
                self._go_page(first_accessible_page())
        if auth.current_session() is None:
            self._lock_session()

    def _suspend_session_verification(self) -> None:
        """Keep drafts and the registered token while retrying an unavailable check."""
        if not getattr(self, '_session_verification_pending', False):
            _LOG.warning("Contrôle de session indisponible ; accès suspendu, nouvelle tentative automatique.", exc_info=True)
        self._session_verification_pending = True
        self._session_lock_overlay.set_context(
            title="Connexion temporairement indisponible",
            detail="Vérification de votre session en attente. Nouvelle tentative automatique.\n"
                   "Vos saisies restent dans la fenêtre. Si la session est toujours valide, "
                   "le travail reprendra sans nouvelle connexion.",
        )
        self._session_lock_overlay.sync_geometry()
        self._session_lock_overlay.show()
        self._session_lock_overlay.raise_()

    def _lock_session(self, *, detail: str | None = None) -> None:
        self._begin_relogin_guard(
            username_hint=(self._auth_session.username if self._auth_session else "") or "",
            title="Session verrouillée",
            detail=detail
            or (
                "Reconnectez-vous pour continuer.\n"
                "Les données à l'écran restent masquées tant que la session n'est pas rétablie."
            ),
        )

    def _change_own_password(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from sme_gend.services.auth_context import get_app_auth
        from sme_gend.ui.change_password_dialog import ChangeOwnPasswordDialog

        auth = get_app_auth()
        if auth is None or not auth.is_enforced() or self._session_lock_active:
            return
        session = auth.current_session()
        if session is None:
            QMessageBox.warning(
                self,
                "Mot de passe",
                "Session expirée — reconnectez-vous.",
            )
            return
        dlg = ChangeOwnPasswordDialog(auth, session.username, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        QMessageBox.information(
            self,
            "Mot de passe",
            "Votre mot de passe a été mis à jour.",
        )

    def _logout_user(self) -> None:
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is None or not auth.is_enforced() or self._session_lock_active:
            return
        from sme_gend.ui.confirm_dialogs import confirm_yes_no

        if not confirm_yes_no(
            self,
            "Se déconnecter",
            "Fermer votre session et afficher l'écran de connexion ?\n\n"
            "Les données non enregistrées en cours de saisie peuvent être perdues.",
            default_no=True,
        ):
            return
        self._begin_relogin_guard(
            username_hint="",
            title="Déconnecté",
            detail=(
                "Connectez-vous avec un compte autorisé pour reprendre le travail.\n"
                "L'application reste ouverte en attendant."
            ),
        )

    def _begin_relogin_guard(
        self,
        *,
        username_hint: str,
        title: str,
        detail: str,
    ) -> None:
        if self._session_lock_active:
            return
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is None or not auth.is_enforced():
            return
        self._session_lock_active = True
        self._session_verification_pending = False
        self._locked_username = username_hint
        self._auth_session = None
        auth.logout()
        timer = getattr(self, "_session_guard_timer", None)
        if timer is not None:
            timer.stop()
        self._session_lock_overlay.set_context(title=title, detail=detail)
        self._session_lock_overlay.refresh_theme()
        self._session_lock_overlay.sync_geometry()
        self._session_lock_overlay.show()
        self._session_lock_overlay.raise_()
        QTimer.singleShot(0, self._prompt_relogin)

    def _prompt_relogin(self) -> None:
        from sme_gend.services.auth_context import get_app_auth
        from sme_gend.ui.login_dialog import LoginDialog

        auth = get_app_auth()
        if auth is None:
            return
        ok, session, quit_requested = LoginDialog.run_relogin(
            auth,
            self,
            username_hint=self._locked_username,
        )
        if ok and session is not None:
            self._on_session_restored(session)
            return
        if quit_requested:
            self.close()
            return
        QTimer.singleShot(0, self._prompt_relogin)

    def _on_session_restored(self, session) -> None:
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is not None:
            auth.reload_config()
        self._auth_session = session
        self._session_lock_active = False
        self._session_lock_overlay.hide()
        self._sidebar_sub.setText(session.display_name or session.username)
        self._apply_nav_permissions()
        self._pilotage_view._sme_panel.refresh_workflow()
        self._cycle_archives_view.on_page_activated()
        cur = self._stack.currentIndex()
        if not session_can_access_page(cur):
            self._go_page(first_accessible_page())
        self.statusBar().showMessage(
            f"Session rétablie — {session.display_name or session.username}",
            5000,
        )
        timer = getattr(self, "_session_guard_timer", None)
        if timer is not None:
            timer.start()

    def _finish_startup_refresh(self) -> None:
        """Affiche la fenêtre d'abord, puis recharge les données (MySQL) en différé."""
        QTimer.singleShot(200, self._deferred_startup_data_refresh)
        QTimer.singleShot(1500, self._warm_mysql_pool)
        QTimer.singleShot(4000, self._ensure_attestation_wiring)
        QTimer.singleShot(8000, self._start_prewarm_lazy_pages)

    def _deferred_startup_data_refresh(self) -> None:
        try:
            self.refresh_for_month_change()
        finally:
            self._home_bootstrapping = False

    def _warm_mysql_pool(self) -> None:
        """Ouvre une connexion MySQL en arrière-plan (pool prêt pour la navigation)."""
        self._check_mysql_health(silent=True)

    def _check_mysql_health(self, *, silent: bool = False) -> None:
        from sme_gend.services.mysql_health import probe_mysql
        from sme_gend.ui.bg_task import run_in_background

        if not silent:
            self._context_banner.set_mysql_status(None)

        st = self._settings

        def _on_result(probe) -> None:
            self._mysql_online = probe.ok
            self._context_banner.set_mysql_status(probe.ok, probe.message)
            if not probe.ok:
                from sme_gend.repositories.mysql_pool import invalidate_mysql_pool

                invalidate_mysql_pool(st)
                if not silent:
                    self.statusBar().showMessage(
                        "MySQL indisponible — cliquez le bandeau rouge pour reconfigurer.",
                        8000,
                    )

        def _on_error(exc: BaseException) -> None:
            from sme_gend.services.mysql_health import format_mysql_error_for_user

            self._mysql_online = False
            msg = format_mysql_error_for_user(exc)
            self._context_banner.set_mysql_status(False, msg)
            from sme_gend.repositories.mysql_pool import invalidate_mysql_pool

            invalidate_mysql_pool(st)

        run_in_background(
            self,
            lambda: probe_mysql(st),
            _on_result,
            on_error=_on_error,
        )

    def _start_prewarm_lazy_pages(self) -> None:
        """Construit en arrière-plan les pages admin souvent ouvertes (navigation instantanée)."""
        from sme_gend.ui.navigation_pages import (
            PAGE_BACKUP_RESTORE,
            PAGE_DOC_GEN_LOG,
            PAGE_LIMITE_AGE,
            PAGE_SETTINGS,
            PAGE_SOIT_TRANSMIS,
            PAGE_USERS_AUTH,
        )

        self._prewarm_queue = [
            p
            for p in (
                PAGE_SETTINGS,
                PAGE_LIMITE_AGE,
                PAGE_SOIT_TRANSMIS,
            )
            if session_can_access_page(p)
        ]
        self._prewarm_next_lazy_page()

    def _prewarm_next_lazy_page(self) -> None:
        if not self._prewarm_queue:
            return
        idx = self._prewarm_queue.pop(0)
        host = self._stack.widget(idx)
        if isinstance(host, LazyStackPage) and host.built_widget() is None:
            host.ensure_built()
        QTimer.singleShot(2000, self._prewarm_next_lazy_page)

    def apply_login_session(self, session) -> None:
        """Après connexion (fenêtre déjà construite pendant le splash)."""
        self._auth_session = session
        self._consulted_index = None
        self._btn_logout_sidebar.setEnabled(session is not None)
        self._session_menu.setEnabled(session is not None)
        if session is not None:
            self._sidebar_sub.setText(session.display_name or session.username)
        self._apply_nav_permissions()
        self._install_session_guard()

    def _page_widget(self, index: int, *, wait_build: bool = False) -> QWidget | None:
        host = self._stack.widget(index)
        if host is None:
            return None
        if isinstance(host, LazyStackPage):
            if wait_build:
                host.ensure_built()
                while host.built_widget() is None and host.build_error() is None:
                    app = QApplication.instance()
                    if app is None:
                        break
                    app.processEvents()
                return host.built_widget()
            return host.built_widget()
        return host

    def _on_lazy_page_built(self, index: int, _child: object) -> None:
        if self._stack.currentIndex() != index:
            return
        self._wire_liste_layout_if_ready()
        if index == PAGE_LISTE_GENERALE and self._personnel_list_view is not None:
            pl = self._personnel_list_view
            if self._pending_liste_structure_filter:
                pl.apply_structure_filter(self._pending_liste_structure_filter)
                self._pending_liste_structure_filter = None
            if self._pending_liste_search:
                pl._search.setText(self._pending_liste_search)
                pl._apply_filter()
                pl._search.setFocus()
                self._pending_liste_search = None
        if index == PAGE_FONCTIONS:
            from sme_gend.ui.views.fonction_referentiel_view import FonctionsView as _FV

            fv = self._fonctions_view
            if fv is None and isinstance(_child, _FV):
                self._fonctions_view = _child
                fv = _child
            if fv is not None and self._pending_fonctions_structure:
                fv.focus_structure(self._pending_fonctions_structure)
                self._pending_fonctions_structure = None
        if index == PAGE_GAIN_PERTE:
            self._apply_pending_gain_perte_nav()
        if is_avancement_page(index):
            self._apply_pending_avancement_nav()
        if index in (PAGE_LIMITE_AGE, PAGE_SOIT_TRANSMIS):
            self._ensure_attestation_wiring()
        on_act = getattr(_child, "on_page_activated", None)
        if callable(on_act):
            on_act()
        from sme_gend.ui.page_search import page_search_field
        self._context_banner.set_filter_available(page_search_field(_child) is not None)
        self.statusBar().showMessage("Vue prête.", 2000)
        self._trace_visible_section(index)

    def _refresh_chrome_after_nav(self) -> None:
        from sme_gend.ui.page_search import page_search_field
        self._context_banner.set_filter_available(page_search_field(self._page_widget(self._stack.currentIndex())) is not None)
        display = self._display_mois()
        operational = self._operational_mois()
        if self._consultation_mois and operational and display != operational:
            self._period_month_lbl.setText(
                f"{_format_mois_fr(display)} (travail : {_format_mois_fr(operational)})"
            )
        else:
            self._period_month_lbl.setText(_format_mois_fr(display))

        from sme_gend.services.sme_editing_policy import peek_editing_state

        view_mois = display or None
        cached = peek_editing_state(self._settings, view_mois=view_mois)
        if cached is not None:
            self._apply_editing_state_to_chrome(cached)
            self._context_banner.refresh_from_state(cached)
        elif not self._chrome_editing_busy:
            self._chrome_editing_busy = True
            self._period_statut_lbl.setText("…")
            from sme_gend.ui.bg_task import run_in_background
            from sme_gend.services.sme_editing_policy import evaluate_editing_state

            def done(st: object) -> None:
                self._chrome_editing_busy = False
                if self._stack.currentIndex() != self._last_nav_index:
                    return
                self._apply_editing_state_to_chrome(st)
                self._context_banner.refresh_from_state(st)

            run_in_background(
                self,
                lambda: evaluate_editing_state(self._settings, view_mois=view_mois),
                done,
            )

    def _ensure_attestation_wiring(self) -> None:
        if self._attestation_wired:
            return
        if self._limite_age_view is None:
            self._limite_age_view = self._built_page_widget(PAGE_LIMITE_AGE, LimiteAgeView)
        if self._soit_transmis_view is None:
            self._soit_transmis_view = self._built_page_widget(
                PAGE_SOIT_TRANSMIS, SoitTransmisView
            )
        la, st = self._limite_age_view, self._soit_transmis_view
        if la is None or st is None:
            return
        self._attestation_wired = True
        st.set_limite_age_service(la.limite_age_service)

        def _push_to_soit_transmis(persons, grade_code_map=None, results=None):
            st.load_persons(persons, grade_code_map, results)
            self._go_page(PAGE_SOIT_TRANSMIS)

        la.set_send_to_soit_transmis_fn(_push_to_soit_transmis)

    def _built_page_widget(self, index: int, expected_type: type) -> QWidget | None:
        host = self._stack.widget(index)
        if isinstance(host, LazyStackPage):
            widget = host.built_widget()
        else:
            widget = host
        return widget if isinstance(widget, expected_type) else None

    def is_attestation_wiring_ready(self) -> bool:
        return self._attestation_wired

    def _wire_liste_layout_if_ready(self) -> None:
        if self._liste_layout_wired:
            return
        le = self._page_widget(PAGE_LAYOUT_EDITOR, wait_build=False)
        lp = self._page_widget(PAGE_LISTE_PRESENTS, wait_build=False)
        if le is None or lp is None:
            return
        from sme_gend.ui.views.layout_editor_view import LayoutEditorView
        from sme_gend.ui.views.liste_present_view import ListePresentView

        if isinstance(le, LayoutEditorView) and isinstance(lp, ListePresentView):
            lp.set_open_layout_editor(lambda: self._go_page(PAGE_LAYOUT_EDITOR))
            le.set_on_saved_callback(lp.apply_runtime_after_config_change)
            self._liste_layout_wired = True

    def _on_nav_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        idx = item.data(0, ROLE_STACK)
        if idx is None:
            return
        try:
            stack_idx = int(idx)
        except (TypeError, ValueError):
            return
        if stack_idx < 0:
            return
        self._go_page(stack_idx)

    def _update_window_page_title(self, index: int) -> None:
        label = self._nav_labels[index].strip() if 0 <= index < len(self._nav_labels) else ""
        self.setWindowTitle(f"{label} — {WINDOW_TITLE}" if label else WINDOW_TITLE)

    def _on_content_page_changed(self, index: int) -> None:
        self._update_window_page_title(index)
        prev = self._chrome_nav_index
        self._chrome_nav_index = index
        self._last_nav_index = index
        if index != prev:
            self._chrome_refresh_timer.start()
        QTimer.singleShot(0, lambda idx=index: self._notify_page_activated(idx))

    def _notify_page_activated(self, index: int) -> None:
        if self._stack.currentIndex() != index:
            return
        w = self._page_widget(index, wait_build=False)
        if w is None:
            return
        on_act = getattr(w, "on_page_activated", None)
        if callable(on_act):
            on_act()

        self._trace_visible_section(index)

    def _trace_visible_section(self, index):
        if getattr(self, '_consulted_index', None) == index or not session_can_access_page(index):
            return
        from sme_gend.services.consultation_audit import queue_consultation
        section = self._nav_labels[index] if 0 <= index < len(self._nav_labels) else str(index)
        self._consulted_index = index
        def failed(_exc):
            self._consulted_index = None
            self.statusBar().showMessage("Consultation affichée, mais traçabilité indisponible. Vérifiez MySQL ou l'état de maintenance.", 0)
        queue_consultation(self, self._settings, action='section_consulted', section=section, on_error=failed)

    def notify_effectifs_liste_changed(self) -> None:
        """
        Après une modification hors Liste / Édition SME (ex. module Stages),
        invalide le cache partagé et force le rechargement des grilles effectifs.
        """
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(self._settings)
        pl = self._personnel_list_view
        if pl is not None:
            pl.mark_dirty()
            if pl.isVisible():
                pl.on_page_activated()
        if self._edition_sme_view is not None:
            self._edition_sme_view.mark_dirty()

    def _recover_local_journals(self) -> None:
        if self._journal_recovery_busy or QApplication.activeModalWidget() is not None:
            return
        from sme_gend.services.audit_mirror_recovery import recover_pending, enabled_mirror_kinds
        from sme_gend.ui.bg_task import run_in_background
        self._journal_recovery_busy = True

        def loaded(result):
            self._journal_recovery_busy = False
            if result["failed"]:
                self.statusBar().showMessage("Journaux locaux en attente : vérifiez l'accès au dossier. Reprise automatique conservée.", 8000)
            elif result["delivered"]:
                self.statusBar().showMessage(f"{result['delivered']} événement(s) de journal récupéré(s).", 5000)

        def failed(exc):
            self._journal_recovery_busy = False
            _LOG.warning("Reprise des journaux indisponible ; les livraisons persistantes sont conservées.")
            self.statusBar().showMessage("Reprise des journaux indisponible : vérifiez MySQL et le profil audit_delivery. Les copies en attente sont conservées.", 8000)

        self._journal_recovery_worker = run_in_background(
            self, lambda: recover_pending(self._settings, enabled_kinds=enabled_mirror_kinds()), loaded, on_error=failed,
        )

    def _shared_refresh_blocked(self) -> bool:
        if QApplication.activeModalWidget() is not None:
            return True
        dirty = getattr(self, "_has_unsaved_changes", None)
        if callable(dirty) and dirty():
            return True
        from PySide6.QtWidgets import QAbstractItemView
        page = self._page_widget(self._stack.currentIndex(), wait_build=False)
        return any(isinstance(table, QAbstractItemView) and table.state() == QAbstractItemView.State.EditingState
                   for table in (getattr(page, '_table', None), getattr(page, '_tbl', None)))

    def _set_shared_sync_status(self, state, detail=""):
        from datetime import datetime
        if state == "ok":
            self._sync_last_success = datetime.now().strftime("%H:%M:%S")
        self._sync_state = state
        last = getattr(self, "_sync_last_success", None)
        labels = {"ok":"contrôle réussi", "available":"mise à jour disponible", "pending":"contrôle en cours", "deferred":"différée pendant la saisie", "error":"erreur — réessayer"}
        text = "Synchronisation : " + labels.get(state,state)
        if last and state != 'available':
            text += " · vérifiée à " + last
        label = getattr(self,"_sync_status_label",None)
        if label is not None:
            label.setText(text)
            label.setToolTip(detail or text)

    def _refresh_shared_data_now(self) -> None:
        self._poll_shared_data_revision(refresh=True)

    def _poll_shared_data_revision(self, *, refresh: bool = False) -> None:
        if refresh:
            self._shared_manual_refresh_pending = True
        refresh = refresh or getattr(self, '_shared_manual_refresh_pending', False)
        if self._shared_revision_busy:
            if refresh:
                ShellWindow._set_shared_sync_status(self, 'pending',
                    'Votre demande F5 est enregistrée ; la vue sera rechargée à la fin du contrôle en cours.')
            return
        if ShellWindow._shared_refresh_blocked(self):
            ShellWindow._set_shared_sync_status(self,"deferred", "Vos saisies sont conservées. Le prochain contrôle reprendra après leur enregistrement ou la fermeture du dialogue.")
            return
        from sme_gend.services.data_sync import read_data_revision
        from sme_gend.services.shared_media import invalidate_shared_media_cache
        from sme_gend.repositories.mysql_connection import mysql_database_key
        from sme_gend.ui.bg_task import run_in_background
        settings = self._settings
        database = mysql_database_key(settings)
        self._shared_revision_busy = True
        ShellWindow._set_shared_sync_status(self,"pending")

        def loaded(token):
            try:
                # A dialog, editor or profile change may have started while SQL ran.
                # Leave the token unacknowledged so the next poll retries safely.
                if database != mysql_database_key(self._settings):
                    return
                if ShellWindow._shared_refresh_blocked(self):
                    ShellWindow._set_shared_sync_status(self,"deferred")
                    return
                if refresh or getattr(self, '_shared_manual_refresh_pending', False):
                    invalidate_shared_media_cache()
                    # Rebind only when shared data actually changed. The normal
                    # manual reload refreshes the visible page once.
                    if self._shared_revision != token:
                        self.refresh_for_month_change(strict=True, refresh_current=False)
                    self._reload_current_view()
                    self._shared_manual_refresh_pending = False
                    self._shared_revision = token
                    self._shared_revision_pending = None
                    self.statusBar().showMessage("Rechargement de la vue depuis MySQL demandé.", 4000)
                elif self._shared_revision is None:
                    # Startup already loads the views; establish a baseline only.
                    self._shared_revision = token
                elif self._shared_revision != token:
                    self._shared_revision_pending = token
                    ShellWindow._set_shared_sync_status(self,"available",
                        "Des modifications sont disponibles. Votre page et vos recherches sont conservées. Cliquez sur Actualiser ou appuyez sur F5 quand vous êtes prêt.")
                    return
                else:
                    self._shared_revision_pending = None
                ShellWindow._set_shared_sync_status(self,"ok")
            except Exception:
                ShellWindow._set_shared_sync_status(self,"error", "Actualisation incomplète ; les données affichées peuvent être anciennes. Cliquez sur Actualiser pour réessayer.")
                _LOG.exception("Actualisation distante interrompue ; reprise au prochain contrôle.")
                self.statusBar().showMessage("Actualisation incomplète. Cliquez sur Actualiser pour réessayer.", 8000)
            finally:
                self._shared_revision_busy = False

        def failed(exc):
            self._shared_revision_busy = False
            if database == mysql_database_key(self._settings):
                ShellWindow._set_shared_sync_status(self,"error", "Connexion MySQL indisponible. Les données affichées peuvent être anciennes. Utilisez Vérifier la synchronisation après rétablissement de la connexion.")
            _LOG.debug("Synchronisation des postes indisponible : %s", exc)

        try:
            self._shared_revision_worker = run_in_background(
                self, lambda: read_data_revision(settings), loaded, on_error=failed,
            )
        except Exception as exc:
            failed(exc)

    def _focus_current_filter(self) -> None:
        from sme_gend.ui.page_search import page_search_field
        field = page_search_field(self._page_widget(self._stack.currentIndex()))
        if field is not None:
            field.setFocus(Qt.FocusReason.ShortcutFocusReason)
            field.selectAll()

    def _reload_current_view(self) -> None:
        if ShellWindow._shared_refresh_blocked(self):
            ShellWindow._set_shared_sync_status(self,"deferred")
            self.statusBar().showMessage("Actualisation différée : terminez ou enregistrez la saisie en cours.", 5000)
            return
        from sme_gend.services.shared_media import invalidate_shared_media_cache
        invalidate_shared_media_cache()
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
        from sme_gend.padmin_present import invalidate_padmin_presence_cache
        invalidate_liste_cache()
        invalidate_editing_state_cache()
        invalidate_padmin_presence_cache()
        w = self._page_widget(self._stack.currentIndex())
        if w is None:
            return
        if hasattr(w, 'refresh_from_database'):
            if hasattr(w, "mark_view_stale"):
                w.mark_view_stale()
            w.refresh_from_database()
        else:
            # mark_dirty may itself start a load. Explicit database refreshes
            # own their single load and preserve the current search/selection.
            if hasattr(w, "mark_view_stale"):
                w.mark_view_stale()
            elif hasattr(w, "mark_dirty"):
                w.mark_dirty()
            if hasattr(w, "load_data"):
                w.load_data()
            elif hasattr(w, "_reload_stats"):
                w._reload_stats(silent=False)
            elif hasattr(w, "on_page_activated"):
                w.on_page_activated()
            elif hasattr(w, "apply_runtime_after_config_change"):
                w.apply_runtime_after_config_change()
        from sme_gend.services.sme_editing_policy import peek_editing_state

        st = peek_editing_state(self._settings)
        if st is not None:
            self._context_banner.refresh_from_state(st)
        else:
            self._context_banner.refresh()
        self.statusBar().showMessage("Rechargement de la vue lancé depuis MySQL.", 4000)

    def _go_prev_month(self) -> None:
        self._navigate_month(-1)

    def _go_next_month(self) -> None:
        self._navigate_month(+1)

    def _month_nav_bounds(self) -> tuple[bool, bool]:
        """Retourne (peut_reculer, peut_avancer) selon les mois connus en base."""
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService
        current = self._display_mois()

        try:
            mois_list = SmeWorkspaceService(self._settings).list_known_months()
        except Exception:
            mois_list = []

        if not mois_list or current not in mois_list:
            return True, True

        idx = mois_list.index(current)
        return idx > 0, idx < len(mois_list) - 1

    def _update_month_nav_buttons(self) -> None:
        can_prev, can_next = self._month_nav_bounds()
        self._btn_prev_month.setEnabled(can_prev)
        self._btn_next_month.setEnabled(can_next)
        self._btn_prev_month.setToolTip(
            "Mois précédent" if can_prev else "Aucun mois précédent"
        )
        self._btn_next_month.setToolTip(
            "Mois suivant" if can_next else "Aucun mois suivant"
        )
        from sme_gend.services.workflow_permissions import session_can_perform_action

        can_close = session_can_perform_action("workflow.close_month")
        self._btn_close_month.setEnabled(can_close)
        close_ttip = (
            "Clôturer le mois actif, archiver la mise en page et activer le mois suivant "
            "(mise à jour sme_periode en base).\n\n"
            "Ce n'est pas la navigation ◄ ► : les flèches changent seulement le mois consulté, "
            "sans clôture ni archive."
            if can_close
            else "Vous n'avez pas la permission de clôturer un mois."
        )
        self._btn_close_month.setToolTip(close_ttip)
        self._btn_close_month_compact.setEnabled(can_close)
        self._btn_close_month_compact.setToolTip(close_ttip)

    def _open_month_closure_from_sidebar(self) -> None:
        self._pilotage_view.open_closure_dialog(parent=self)

    def _display_mois(self) -> str:
        """Mois affiché dans les vues (consultation ou mois de travail)."""
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        if self._consultation_mois:
            return canonical_sme_mois_yyyy_mm(self._consultation_mois) or self._consultation_mois
        ctx = load_sme_context()
        return canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or ""

    def _operational_mois(self) -> str:
        """Mois de travail opérationnel (sme_periode « Ouvert »), indépendant de la consultation."""
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService

        try:
            return canonical_sme_mois_yyyy_mm(
                SmeWorkspaceService(self._settings).get_active_mois()
            ) or ""
        except Exception:
            return canonical_sme_mois_yyyy_mm(load_sme_context().mois_actif or "") or ""

    def _activate_period(self, target: str) -> None:
        """Change only this workstation's displayed month, never global statuses."""
        from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        if self._has_unsaved_changes():
            QMessageBox.warning(self, "Brouillons en cours", "Enregistrez vos modifications avant de changer de mois.")
            return
        ym = canonical_sme_mois_yyyy_mm(target)
        if not ym:
            return

        invalidate_editing_state_cache()

        op = self._operational_mois()
        self._consultation_mois = None if op == ym else ym
        self.refresh_for_month_change()

    def _navigate_month(self, direction: int) -> None:
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService
        current = self._display_mois()

        mois_list: list[str] = []
        try:
            mois_list = SmeWorkspaceService(self._settings).list_known_months()
        except Exception:
            pass

        new_mois: str | None = None
        if mois_list:
            if current in mois_list:
                new_idx = mois_list.index(current) + direction
                if 0 <= new_idx < len(mois_list):
                    new_mois = mois_list[new_idx]
            else:
                new_mois = mois_list[-1] if direction > 0 else mois_list[0]
        elif current:
            try:
                y, m = int(current[:4]), int(current[5:7])
                m += direction
                if m < 1:
                    m, y = 12, y - 1
                elif m > 12:
                    m, y = 1, y + 1
                new_mois = f"{y:04d}-{m:02d}"
            except (ValueError, IndexError):
                pass

        if new_mois:
            self._activate_period(new_mois)
        else:
            self._update_month_nav_buttons()

    def _refresh_active_views_on_month_change(self, *, refresh_current: bool = True) -> None:
        from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache

        invalidate_editing_state_cache()
        new_mois = self._display_mois() or None
        for i in range(self._stack.count()):
            host = self._stack.widget(i)
            if isinstance(host, LazyStackPage):
                w = host.built_widget()
            else:
                w = host
            if w is None:
                continue
            if isinstance(w, CycleArchivesView):
                w.set_active_mois(new_mois)
            if hasattr(w, "mark_dirty"):
                w.mark_dirty()

        if not refresh_current:
            return
        if not getattr(self, "_home_bootstrapping", False):
            self._pilotage_view._sme_panel.refresh_workflow()

        current_idx = self._stack.currentIndex()
        if current_idx == PAGE_DASHBOARD and not getattr(self, "_home_bootstrapping", False):
            self._home_view._reload_stats(silent=True)
        elif current_idx == PAGE_ARCHIVES_CLOSURE and not getattr(self, "_home_bootstrapping", False):
            self._cycle_archives_view.on_page_activated()
        elif current_idx == PAGE_LIMITE_AGE:
            w = self._page_widget(current_idx)
            if w is not None and hasattr(w, "on_page_activated"):
                w.on_page_activated()
        else:
            current = self._page_widget(current_idx)
            if current is not None and hasattr(current, "apply_runtime_after_config_change"):
                current.apply_runtime_after_config_change()

    def _rebind_data_services(
        self,
        data_settings: Settings,
        ref_settings: Settings | None = None,
    ) -> None:
        """Rebinde les services de données.

        data_settings : source des données mensuelles (ListeGenerale).
        ref_settings  : source du référentiel (structures, grades, subdivisions).
                        Si None, identique à data_settings (mode live normal).
        """
        if ref_settings is not None:
            self._dashboard.rebind_for_archive(data_settings, ref_settings)
        else:
            self._dashboard.rebind_settings(data_settings)
        self._personnel.rebind_settings(data_settings)
        self._layout_cfg.rebind_settings(data_settings)
        self._liste_present.rebind_settings(data_settings)
        self._numerique_preview.rebind_settings(
            data_settings,
            gain_perte_settings=(ref_settings if ref_settings is not None else data_settings),
        )
        self._detaches_preview.rebind_settings(data_settings)
        if self._limite_age_view is not None:
            self._limite_age_view.rebind_settings(data_settings)
        if self._soit_transmis_view is not None:
            self._soit_transmis_view.rebind_settings(data_settings)
        self._referentiel.rebind_settings(data_settings)
        if self._edition_sme_view is not None:
            self._edition_sme_view.rebind_settings(data_settings)
        if self._sme_dpf_view is not None:
            self._sme_dpf_view.rebind_settings(data_settings)
        self._context_banner.rebind_settings(data_settings)
        if self._backup_restore_view is not None:
            self._backup_restore_view.rebind_settings(data_settings)
        if self._avancement_view is not None:
            self._avancement_view.rebind_settings(data_settings)
        from sme_gend.ui.views.career_alerts_view import CareerAlertsView
        for i in range(self._stack.count()):
            page = self._page_widget(i)
            if isinstance(page, CareerAlertsView):
                page.apply_runtime_settings(data_settings)

    def _apply_month_data_source(self, mois: str) -> None:
        self._consulted_index = None
        """
        Mois consulté (fermé ou import historique) : filtre la liste sur la carrière MySQL
        de ce mois (``mysql_view_mois``). Mois de travail ouvert : situation « actuelle »
        (``est_actuelle = 1``).
        Ne relit pas le .mdb du dossier archives — l'import Access → MySQL reste manuel (①).
        """
        import dataclasses

        from PySide6.QtWidgets import QMessageBox

        from sme_gend.config.sme_context import is_mois_ferme
        from sme_gend.config.liste_generale_scope import should_filter_liste_for_display_mois
        from sme_gend.services.sme_month_closure import find_archive_for_month

        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        ym = canonical_sme_mois_yyyy_mm(mois) or mois
        operational = self._operational_mois()
        use_historical_view = should_filter_liste_for_display_mois(
            ym,
            operational_mois=operational,
            consultation_mois=self._consultation_mois,
            month_is_closed=is_mois_ferme(mois),
        )

        if use_historical_view:
            view_st = dataclasses.replace(self._settings, mysql_view_mois=ym)
            self._rebind_data_services(view_st)
            arch = find_archive_for_month(mois)
            from sme_gend.services.sme_workspace_service import SmeWorkspaceService

            in_mysql = ym in set(SmeWorkspaceService(self._settings).list_mysql_months())
            if arch and not in_mysql:
                self._period_source_lbl.setText(
                    f"⚠ Archive {arch.name} — données du mois absentes de MySQL"
                )
                self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_ARCHIVE)
                self.statusBar().showMessage(
                    f"Mois {mois} : fichier archive présent sans données MySQL — "
                    f"utilisez « {IMPORT_MONTH_ACCESS_ACTION} ».",
                    12000,
                )
            elif arch:
                self._period_source_lbl.setText(
                    f"Historique MySQL · {mois} (archive {arch.name} disponible)"
                )
                self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_ARCHIVE)
            else:
                self._period_source_lbl.setText(
                    f"Historique MySQL · {mois} (carrière en base, sans fichier archive)"
                )
                self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_ARCHIVE)
        else:
            live_st = dataclasses.replace(self._settings, mysql_view_mois=None)
            self._rebind_data_services(live_st)
            self._period_source_lbl.setText("Base de travail · mois ouvert")
            self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_BASE)

    def _on_months_overview_dialog(self) -> None:
        """Affiche le calendrier des mois : statut périodes SME + présence d'archive pour chacun."""
        from sme_gend.repositories.sme_repository import SmeRepository
        from sme_gend.services.sme_month_closure import (
            canonical_sme_mois_yyyy_mm,
            find_archive_for_month,
        )
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QLabel,
            QTableWidget,
            QTableWidgetItem,
            QDialogButtonBox,
            QHeaderView,
            QMessageBox,
            QPushButton,
            QHBoxLayout,
        )
        from PySide6.QtGui import QColor

        from sme_gend.config.sme_context import is_mois_ferme
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService

        ctx = load_sme_context()
        mois_actif = (ctx.mois_actif or "").strip()
        ws = SmeWorkspaceService(self._settings)
        fermes_set = set(ws.list_closed_months())

        tabsme_map: dict[str, str] = {}
        try:
            repo = SmeRepository(self._settings)
            if repo.table_exists():
                for r in repo.fetch_all():
                    ym = canonical_sme_mois_yyyy_mm(r.mois)
                    if ym:
                        tabsme_map[ym] = r.statut or "—"
        except Exception:
            pass

        all_months: set[str] = set(ws.list_known_months()) | set(tabsme_map.keys()) | fermes_set
        if mois_actif:
            ym = canonical_sme_mois_yyyy_mm(mois_actif)
            if ym:
                all_months.add(ym)

        sorted_months = sorted(all_months, reverse=True)

        dlg = QDialog(self)
        dlg.setWindowTitle("Changer de période — calendrier des mois")
        dlg.setMinimumWidth(560)
        dlg.setMinimumHeight(420)
        lay = QVBoxLayout(dlg)

        arch_hint = ""
        try:
            from sme_gend.config.user_paths import resolved_sme_archives_directory

            arch_hint = f" ({resolved_sme_archives_directory()})"
        except Exception:
            pass
        known_months = set(ws.list_mysql_months())
        op_mois = self._operational_mois()
        info = QLabel(
            "Liste des mois (<code>sme_periode</code> + fichiers <code>sme_archives/</code>). "
            f"<b>Archive disque</b> = copie .mdb{arch_hint}. "
            "<b>Données MySQL</b> = période en base (import ① requis pour la liste générale).<br><br>"
            f"<b>Mois de travail actuel :</b> {op_mois or '—'}.<br>"
            "Double-clic : <b>consulter</b> un mois sans changer le mois de travail des autres postes. "
            "L'activation est réservée au bouton « Définir comme mois de travail » ; "
            "un mois clôturé doit d'abord être réouvert avec un motif dans le pilotage SME."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        tbl = QTableWidget(len(sorted_months), 5)
        tbl.setHorizontalHeaderLabels(
            ["Mois", "Statut périodes SME", "Archive disque", "MySQL", "État"]
        )
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        for row, ym in enumerate(sorted_months):
            archive = find_archive_for_month(ym)
            statut = tabsme_map.get(ym, "—")
            is_ferme = ym in fermes_set or is_mois_ferme(ym)
            is_actif = ym == canonical_sme_mois_yyyy_mm(mois_actif)

            in_mysql = ym in known_months
            is_displayed = ym == self._display_mois()
            is_operational = ym == op_mois

            if is_operational and is_displayed:
                etat = "● Mois de travail"
                row_color = QColor("#e8f5e9")
            elif is_displayed and self._consultation_mois:
                etat = "◐ Consultation"
                row_color = QColor("#e1f5fe")
            elif archive is not None and not in_mysql:
                etat = "Archive seule (import ①)"
                row_color = QColor("#fff3e0")
            elif archive is not None:
                etat = "Archive + MySQL"
                row_color = QColor("#e3f2fd")
            elif is_ferme:
                etat = "Clôturé" + (" · MySQL" if in_mysql else " · vide")
                row_color = QColor("#fff8e1")
            elif in_mysql:
                etat = "Ouvert / en base"
                row_color = None
            else:
                etat = "—"
                row_color = None

            tbl.setItem(row, 0, QTableWidgetItem(ym))
            tbl.setItem(row, 1, QTableWidgetItem(statut))
            tbl.setItem(row, 2, QTableWidgetItem(
                f"✓  {archive.name}" if archive else "—"
            ))
            tbl.setItem(row, 3, QTableWidgetItem("✓" if in_mysql else "—"))
            tbl.setItem(row, 4, QTableWidgetItem(etat))

            if row_color:
                # Fond pastel volontairement identique clair/sombre — le texte doit
                # donc être fixé en encre foncée pour rester lisible en thème sombre
                # (sans ça, le texte suit la couleur claire par défaut du thème).
                for col in range(5):
                    item = tbl.item(row, col)
                    if item:
                        item.setBackground(row_color)
                        item.setForeground(QColor("#212121"))

        def _selected_ym() -> str | None:
            rows = tbl.selectionModel().selectedRows()
            if not rows:
                return None
            it = tbl.item(rows[0].row(), 0)
            return it.text().strip() if it else None

        def _on_activate(target: str | None) -> None:
            if not target:
                return
            self._activate_period(target)
            dlg.accept()

        def _on_double_click(r: int, _c: int) -> None:
            it = tbl.item(r, 0)
            if it is None:
                return
            _on_activate(it.text().strip())

        tbl.cellDoubleClicked.connect(_on_double_click)
        lay.addWidget(tbl)

        btn_row = QHBoxLayout()
        btn_consult = QPushButton("Consulter la sélection")
        btn_consult.setToolTip(
            "Affiche ce mois sur ce poste sans modifier les périodes partagées."
        )
        btn_consult.clicked.connect(lambda: _on_activate(_selected_ym()))
        btn_work = QPushButton("Définir comme mois de travail…")
        btn_work.setToolTip(
            "Ouvre ce mois en base (sme_periode « Ouvert ») — à utiliser pour le mois courant de saisie. "
            "Ne remplace pas un import ① pour un mois passé."
        )

        def _on_set_work() -> None:
            from sme_gend.services.auth_guard import require_permission
            if not require_permission(dlg, "workflow.set_active_month"):
                return
            if self._has_unsaved_changes():
                QMessageBox.warning(dlg, "Brouillons en cours", "Enregistrez vos modifications avant de changer le mois de travail.")
                return
            target = _selected_ym()
            if not target:
                return
            if ws.is_month_closed(target):
                QMessageBox.warning(dlg, "Mois clôturé", "Utilisez la réouverture exceptionnelle avec un motif avant d'activer ce mois.")
                return
            from sme_gend.ui.confirm_dialogs import confirm_yes_no

            if not confirm_yes_no(
                dlg,
                "Mois de travail",
                f"Définir « {target} » comme unique mois <b>Ouvert</b> dans sme_periode ?\n\n"
                "Les autres périodes passeront en « Fermé ». "
                "Utilisez plutôt la consultation (double-clic) pour parcourir un mois clôturé.",
                default_no=True,
            ):
                return
            from copy import deepcopy
            ctx2 = deepcopy(ctx)
            ctx2.mois_actif = target
            try:
                save_sme_context(ctx2,force_month_change=True)
            except Exception as exc:
                QMessageBox.critical(dlg,"Mois de travail",str(exc))
                return
            self._consultation_mois = None
            self.refresh_for_month_change()
            dlg.accept()

        btn_work.clicked.connect(_on_set_work)
        from sme_gend.services.auth_guard import session_has_permission
        btn_work.setEnabled(session_has_permission("workflow.set_active_month"))
        btn_row.addWidget(btn_consult)
        btn_row.addWidget(btn_work)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def refresh_for_month_change(self, *, strict: bool = False, refresh_current: bool = True) -> None:
        """
        Rebinde les services sur le mois affiché (consultation ou travail), puis
        rafraîchit le bandeau et marque toutes les vues comme dirty.
        À appeler au démarrage et après tout changement de période (import, clôture, navigation).
        """
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        op = self._operational_mois()
        if op and op != self._last_operational_mois:
            self._consultation_mois = None
        self._last_operational_mois = op

        invalidate_liste_cache()
        mois = self._display_mois()
        if mois:
            try:
                self._apply_month_data_source(mois)
            except Exception as exc:
                if strict:
                    raise
                _LOG.warning("Rebind mois %s : %s", mois, exc, exc_info=True)
                self.statusBar().showMessage(
                    f"Erreur chargement mois {mois} : {exc}",
                    10000,
                )
        else:
            try:
                self._rebind_data_services(self._settings)
            except Exception as exc:
                if strict:
                    raise
                _LOG.warning("Rebind données : %s", exc, exc_info=True)
                self.statusBar().showMessage(
                    f"Erreur rechargement des données : {exc}",
                    10000,
                )
            self._period_source_lbl.setText("Base — définir le mois actif")
            self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_NEUTRAL)
        self._refresh_period_banner()
        self._refresh_active_views_on_month_change(refresh_current=refresh_current)
        self._home_view.mark_dirty()

    def _refresh_period_banner(self) -> None:
        """Bandeau période (sidebar) — requête MySQL si le cache est vide."""
        display = self._display_mois()
        operational = self._operational_mois()
        if self._consultation_mois and operational and display != operational:
            month_txt = (
                f"{_format_mois_fr(display)} "
                f"(travail : {_format_mois_fr(operational)})"
            )
        else:
            month_txt = _format_mois_fr(display)
        self._period_month_lbl.setText(month_txt)
        self._update_month_nav_buttons()
        from sme_gend.services.sme_editing_policy import evaluate_editing_state, peek_editing_state
        from sme_gend.ui.theme import theme_palette

        try:
            st = peek_editing_state(self._settings, view_mois=display) or evaluate_editing_state(
                self._settings, view_mois=display
            )
        except Exception:
            self._period_statut_lbl.setText("⚠ indisponible")
            pal = theme_palette()
            self._period_statut_lbl.setStyleSheet(f"color: {pal['error']};")
            return
        self._apply_editing_state_to_chrome(st)
        self._context_banner.refresh_from_state(st)

    def _apply_editing_state_to_chrome(self, st) -> None:
        from sme_gend.ui.theme import theme_palette

        ctx = load_sme_context()
        mois = (ctx.mois_actif or "").strip()
        pal = theme_palette()
        if st.locked:
            self._period_statut_lbl.setText(
                f"● {st.tabsme_statut or 'Fermé'} — saisie bloquée"
            )
            self._period_statut_lbl.setStyleSheet(f"color: {pal['error']}; font-weight: 600;")
        elif st.tabsme_statut:
            self._period_statut_lbl.setText(f"● {st.tabsme_statut}")
            self._period_statut_lbl.setStyleSheet(f"color: {pal['success']}; font-weight: 600;")
        elif not mois:
            self._period_statut_lbl.setText("Période non définie")
            self._period_statut_lbl.setStyleSheet(f"color: {pal['text_dim']};")
        else:
            self._period_statut_lbl.setText("● Saisie autorisée")
            self._period_statut_lbl.setStyleSheet(f"color: {pal['info']};")

    def _walk_nav_items(self):
        """Parcourt tous les items (y compris enfants)."""
        for i in range(self._nav.topLevelItemCount()):
            yield from self._walk_item(self._nav.topLevelItem(i))

    def _walk_item(self, item: QTreeWidgetItem):
        yield item
        for j in range(item.childCount()):
            yield from self._walk_item(item.child(j))

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()
        bar.setNativeMenuBar(False)

        def _nav_action(page_idx: int) -> QAction:
            act = QAction(self._nav_labels[page_idx], self)
            act.triggered.connect(lambda: self._go_page(page_idx))
            return act

        # ── Fichier ──────────────────────────────────────────────────────────
        m_file = bar.addMenu("&Fichier")
        act_paths = QAction("Configuration MySQL et chemins…", self)
        act_paths.setToolTip(
            "Connexion MySQL (obligatoire), Access (import), modèles Excel, archives. "
            "Rechargement immédiat après enregistrement."
        )
        act_paths.triggered.connect(self._open_paths_config)
        self._act_paths_configuration = act_paths
        m_file.addAction(act_paths)

        m_import = m_file.addMenu("Import & archives")
        act_import = QAction(IMPORT_MONTH_ACCESS_ACTION, self)
        act_import.setToolTip(
            "Charge liste générale + satellites dans MySQL pour le mois choisi. "
            "Archivage du .mdb et clôture du mois optionnels (case à cocher dans l'assistant)."
        )
        act_import.triggered.connect(
            lambda: self._pilotage_view._sme_panel._on_sme_import_dialog()
        )
        m_import.addAction(act_import)
        act_empty_month = QAction("Retirer les imports / vider un mois…", self)
        act_empty_month.triggered.connect(self._on_empty_month_menu)
        m_import.addAction(act_empty_month)

        m_months = m_file.addMenu("Gestion des mois")
        act_fill_month = QAction("Remplir un mois à partir d’un autre…", self)
        act_fill_month.setToolTip("Reprise des effectifs dans un mois vide, sans import. Aperçu et sauvegarde avant enregistrement.")
        act_fill_month.triggered.connect(
            lambda: self._pilotage_view._sme_panel._on_fill_month_dialog()
        )
        m_months.addAction(act_fill_month)

        m_maint = m_file.addMenu("Maintenance")
        act_backfill = QAction("Reconstruire dossiers agents…", self)
        act_backfill.setToolTip(
            "Alimente passations, affectations et diplômes depuis l'historique MySQL."
        )
        act_backfill.triggered.connect(self._on_rebuild_dossiers_menu)
        m_maint.addAction(act_backfill)
        act_reconcile = QAction("Compléter les référentiels carrière…", self)
        act_reconcile.setToolTip(
            "Rattache les libellés des fiches aux référentiels (grade, structure…) "
            "et aligne ordre_tri / presseance ; journalise les anomalies dans etl_erreur."
        )
        act_reconcile.triggered.connect(self._on_reconcile_carriere_menu)
        m_maint.addAction(act_reconcile)

        m_file.addSeparator()
        act_quit = QAction("Quitter\tCtrl+Q", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_session = bar.addMenu("&Session")
        self._session_menu = m_session
        m_session.setEnabled(self._auth_session is not None)
        act_change_pwd = QAction("Changer mon mot de passe…", self)
        act_change_pwd.setToolTip(
            "Modifier votre mot de passe de connexion (ancien mot de passe requis)."
        )
        act_change_pwd.triggered.connect(self._change_own_password)
        m_session.addAction(act_change_pwd)
        m_session.addSeparator()
        act_logout = QAction("Se déconnecter", self)
        act_logout.setToolTip(
            "Fermer la session et afficher l'écran de connexion pour un autre utilisateur."
        )
        act_logout.triggered.connect(self._logout_user)
        m_session.addAction(act_logout)

        # ── Navigation (alignée sur la sidebar — activité métier) ───────────────
        m_nav = bar.addMenu("&Navigation")
        dash_act = _nav_action(PAGE_DASHBOARD)
        m_nav.addAction(dash_act)
        self._nav_menu_actions[PAGE_DASHBOARD] = dash_act

        for group in default_nav_groups():
            submenu = m_nav.addMenu(group.title)
            self._nav_group_menus.append((group, submenu))
            for leaf in group.items:
                label = leaf.label or self._nav_labels[leaf.page_index]
                act = QAction(label, self)
                idx = leaf.page_index
                act.triggered.connect(lambda _checked=False, p=idx: self._go_page(p))
                submenu.addAction(act)
                self._nav_menu_actions[idx] = act

        # ── Affichage ─────────────────────────────────────────────────────────
        m_view = bar.addMenu("&Affichage")
        # Raccourcis via QShortcut (ci-dessus) : pas de setShortcut sur les QAction
        # (sinon Qt signale « Ambiguous shortcut overload »).
        act_search = QAction("Recherche personnel…\tCtrl+K", self)
        act_search.setToolTip(
            "Recherche transversale (personnel, navigation). Raccourci visible aussi sous le menu Aide."
        )
        act_search.triggered.connect(self._open_global_search)
        m_view.addAction(act_search)
        act_reload = QAction("Actualiser la vue courante\tF5", self)
        act_reload.setToolTip("Recharge la page affichée (liste, édition SME, tableau de bord…).")
        act_reload.triggered.connect(self._refresh_shared_data_now)
        m_view.addAction(act_reload)
        act_sidebar = QAction("Réduire / développer la barre latérale", self)
        act_sidebar.setShortcut("Ctrl+B")
        act_sidebar.triggered.connect(self._toggle_sidebar)
        m_view.addAction(act_sidebar)
        from sme_gend.ui.display_controls import DisplayControls
        self._display_controls = DisplayControls(self, m_view)

        m_view.addSeparator()
        m_theme = m_view.addMenu("Thème")
        ctx_theme = load_sme_context()
        self._act_theme_system = QAction("Suivre le système", self)
        self._act_theme_system.setCheckable(True)
        self._act_theme_system.setChecked(ctx_theme.theme == "system")
        self._act_theme_light = QAction("Clair", self)
        self._act_theme_light.setCheckable(True)
        self._act_theme_light.setChecked(ctx_theme.theme == "light")
        self._act_theme_dark = QAction("Sombre", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_dark.setChecked(ctx_theme.theme == "dark")
        theme_grp = QActionGroup(self)
        theme_grp.setExclusive(True)
        theme_grp.addAction(self._act_theme_system)
        theme_grp.addAction(self._act_theme_light)
        theme_grp.addAction(self._act_theme_dark)
        self._act_theme_system.triggered.connect(lambda: self._apply_theme("system"))
        self._act_theme_light.triggered.connect(lambda: self._apply_theme("light"))
        self._act_theme_dark.triggered.connect(lambda: self._apply_theme("dark"))
        m_theme.addAction(self._act_theme_system)
        m_theme.addAction(self._act_theme_light)
        m_theme.addAction(self._act_theme_dark)

        # ── Aide ──────────────────────────────────────────────────────────────
        m_help = bar.addMenu("&Aide")
        m_help.addAction(act_search)
        m_help.addSeparator()
        act_about = QAction(f"À propos de {APP_NAME}…", self)
        act_about.setMenuRole(QAction.MenuRole.NoRole)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _on_empty_month_menu(self) -> None:
        from sme_gend.config.settings import get_settings
        from sme_gend.ui.empty_month_dialog import run_empty_month_dialog

        run_empty_month_dialog(self, get_settings())

    def _on_rebuild_dossiers_menu(self) -> None:
        from sme_gend.ui.dossier_backfill_dialog import run_backfill_dossier_dialog

        run_backfill_dossier_dialog(self, self._settings)

    def _on_reconcile_carriere_menu(self) -> None:
        from sme_gend.ui.carriere_reconciliation_dialog import run_carriere_reconciliation_dialog

        run_carriere_reconciliation_dialog(self, self._settings)

    def _open_paths_config(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "system.settings"):
            return

        from sme_gend.ui.config_paths_dialog import ConfigPathsDialog

        if self._has_unsaved_changes():
            QMessageBox.warning(self, "Brouillons en cours", "Enregistrez vos modifications avant de changer la configuration.")
            return
        try:
            dlg = ConfigPathsDialog(self, settings=self._settings)
        except PermissionError as exc:
            QMessageBox.warning(self, "Droits", str(exc))
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.reload_runtime_configuration(show_success_dialog=True)

    def _apply_theme(self, theme: str) -> None:
        ctx = load_local_sme_context()
        ctx.theme = theme
        save_sme_context(ctx, promote_operational_month=False, save_shared_preferences=False)
        self.apply_theme_from_preference()

    def apply_theme_from_preference(self) -> None:
        """Applique le thème (y compris suivi système) et rafraîchit les vues sensibles."""
        from PySide6.QtWidgets import QApplication

        from sme_gend.ui.theme import apply_app_theme, propagate_theme_refresh

        ctx = load_local_sme_context()
        effective = apply_app_theme(QApplication.instance(), ctx.theme)
        self._act_theme_system.setChecked(ctx.theme == "system")
        self._act_theme_light.setChecked(ctx.theme == "light")
        self._act_theme_dark.setChecked(ctx.theme == "dark")
        self._context_banner.refresh()
        propagate_theme_refresh(self)
        if self._stack.currentIndex() == PAGE_DASHBOARD:
            self._home_view.mark_dirty()
            self._home_view._reload_stats(silent=True)
        self.statusBar().showMessage(
            f"Thème : {'sombre' if effective == 'dark' else 'clair'}"
            + (" (système)" if ctx.theme == "system" else ""),
            4000,
        )

    def _refresh_data_status_label(self) -> None:
        from sme_gend.config.settings import mysql_config_source_label

        s = self._settings
        src = mysql_config_source_label()
        txt = (
            f"Données : MySQL {s.mysql_host}:{s.mysql_port}/{s.mysql_database} "
            f"({src}) · Access (import) : {s.database_path}"
        )
        self._status_data_lbl.setText(txt)
        self._status_data_lbl.setToolTip(txt)

    def reload_runtime_configuration(self, *, show_success_dialog: bool = False) -> bool:
        """Recharge chemins, services et vues après mise à jour de paths.json (sans redémarrage)."""
        from PySide6.QtWidgets import QMessageBox

        try:
            new_settings = get_settings()
        except FileNotFoundError as e:
            QMessageBox.critical(
                self,
                "Configuration",
                str(e),
            )
            return False
        if self._has_unsaved_changes():
            QMessageBox.warning(self, "Brouillons en cours", "Enregistrez vos modifications avant de changer la configuration. La configuration ne sera appliquée qu’après un nouveau rechargement.")
            return False
        self._detaches_cfg.rebind_settings(new_settings)
        self._layout_cfg.rebind_settings(new_settings)
        self._referentiel.rebind_settings(new_settings)
        self._personnel.rebind_settings(new_settings)
        self._liste_present.rebind_settings(new_settings)
        self._dashboard.rebind_settings(new_settings)
        self._numerique_preview.rebind_settings(new_settings)
        self._detaches_preview.rebind_settings(new_settings)
        previous_settings = self._settings
        self._settings = new_settings
        self._consulted_index = None
        self._shared_revision = None
        self._shared_revision_pending = None
        self._sync_last_success = None
        ShellWindow._set_shared_sync_status(self,"pending")
        from sme_gend.services.auth_context import get_app_auth
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        auth = get_app_auth()
        if auth is not None:
            auth.reload_config()
        invalidate_liste_cache(new_settings)
        from sme_gend.repositories.mysql_pool import invalidate_mysql_pool

        invalidate_mysql_pool(previous_settings)
        invalidate_mysql_pool(new_settings)
        self._refresh_data_status_label()
        self._propagate_runtime_settings_to_views()
        self._refresh_period_banner()
        if show_success_dialog:
            QMessageBox.information(
                self,
                "Configuration appliquée",
                "Les chemins et la connexion MySQL ont été rechargés. Les écrans utilisent "
                "désormais la nouvelle configuration.",
            )
        self._check_mysql_health(silent=True)
        return True

    def _apply_runtime_to_view(self, w: QWidget | None) -> None:
        """Propage settings / services vers une vue (y compris contenu LazyStackPage)."""
        if w is None:
            return
        from sme_gend.ui.views.detaches_view import DetachesView
        from sme_gend.ui.views.gain_perte_view import GainPerteView as _GainPerteView
        from sme_gend.ui.views.malade_punis_view import MaladePunisView as _MaladePunisView
        from sme_gend.ui.views.stages_view import StagesView as _StagesView
        from sme_gend.ui.views.missions_view import MissionsView as _MissionsView
        from sme_gend.ui.views.desertion_view import DesertionView as _DesertionView
        from sme_gend.ui.views.conges_view import CongesView as _CongesView
        from sme_gend.ui.views.home_view import HomeView
        from sme_gend.ui.views.cycle_archives_view import CycleArchivesView
        from sme_gend.ui.views.liste_present_view import ListePresentView
        from sme_gend.ui.views.numerique_combined_view import NumeriqueCombinedView as _NumeriqueCombinedView
        from sme_gend.ui.views.personnel_list_view import PersonnelListView
        from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, PadminView, StructuresView
        from sme_gend.ui.views.settings_view import SettingsView
        from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView
        from sme_gend.ui.views.users_auth_view import UsersAuthView
        from sme_gend.ui.views.career_alerts_view import CareerAlertsView
        from sme_gend.ui.views.deleted_personnel_view import DeletedPersonnelView

        s = self._settings
        if isinstance(w, HomeView):
            w.apply_runtime_settings(s)
        elif isinstance(w, CycleArchivesView):
            w.apply_runtime_settings(s)
        elif isinstance(w, PersonnelListView):
            w.apply_runtime_after_config_change()
        elif isinstance(w, ListePresentView):
            w.apply_runtime_after_config_change()
        elif isinstance(w, DetachesView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _NumeriqueCombinedView):
            w.apply_runtime_settings(s)
        elif isinstance(w, (StructuresView, GradesView, FonctionsView, PadminView, DistinctionsView)):
            w.apply_runtime_after_config_change()
        elif isinstance(w, StructureEffectifsView):
            w.apply_runtime_after_config_change()
        elif isinstance(w, SettingsView):
            w.apply_runtime_settings(s)
        elif isinstance(w, CareerAlertsView):
            w.apply_runtime_settings(self._personnel.settings)
        elif isinstance(w, DeletedPersonnelView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _GainPerteView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _MaladePunisView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _StagesView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _MissionsView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _DesertionView):
            w.apply_runtime_settings(s)
        elif isinstance(w, _CongesView):
            w.apply_runtime_settings(s)
        elif isinstance(w, AvancementView):
            w.rebind_settings(s)
        elif isinstance(w, DocGenLogView):
            w.apply_runtime_settings(s)
        elif isinstance(w, SensitiveAuditLogView):
            w.apply_runtime_settings(s)
        elif isinstance(w, UsersAuthView):
            w.mark_view_stale()
            if w.isVisible():
                w.on_page_activated()
        elif isinstance(w, EditionSmeView):
            w.rebind_settings(s)
        elif isinstance(w, LimiteAgeView):
            w.rebind_settings(s)

    def _propagate_runtime_settings_to_views(self) -> None:
        for i in range(self._stack.count()):
            self._apply_runtime_to_view(self._page_widget(i))

    def _refresh_dashboard_if_visible(self) -> None:
        idx = self._stack.currentIndex()
        if idx == PAGE_DASHBOARD:
            self._home_view._reload_stats(silent=False)
            self.statusBar().showMessage("Indicateurs actualisés.", 3000)
        elif idx == PAGE_ARCHIVES_CLOSURE:
            self._cycle_archives_view.on_page_activated()
            self.statusBar().showMessage("Gestion des mois et archives actualisé.", 3000)

    def _apply_nav_permissions(self) -> None:
        """Masque les entrées de menu sans permission (auth activée)."""
        from sme_gend.services.auth_guard import session_has_permission
        config_action = getattr(self, "_act_paths_configuration", None)
        if config_action is not None:
            config_action.setEnabled(session_has_permission("system.settings"))
        for item in self._walk_nav_items():
            idx = item.data(0, ROLE_STACK)
            if idx is None:
                continue
            try:
                stack_idx = int(idx)
            except (TypeError, ValueError):
                continue
            if stack_idx < 0:
                continue
            allowed = session_can_access_page(stack_idx)
            item.setHidden(not allowed)
            item.setDisabled(not allowed)
            act = self._nav_menu_actions.get(stack_idx)
            if act is not None:
                act.setVisible(allowed)
                act.setEnabled(allowed)

        _KIND_FOLDER = "folder"
        for i in range(self._nav.topLevelItemCount()):
            top = self._nav.topLevelItem(i)
            if top is None:
                continue
            if top.data(0, ROLE_KIND) == _KIND_FOLDER or top.childCount() > 0:
                visible = any(
                    not top.child(j).isHidden() for j in range(top.childCount())
                )
                top.setHidden(not visible)

        for group, submenu in self._nav_group_menus:
            vis = any(
                session_can_access_page(leaf.page_index) for leaf in group.items
            )
            submenu.menuAction().setVisible(vis)

    _GP_KPI_FILTER: dict[str, str] = {
        "deces": "DECES",
        "gains_mois": "GAIN",
        "pertes_mois": "PERTE",
    }

    def _go_gain_perte(
        self,
        kpi_id: str,
        *,
        mois_ym: str | None = None,
    ) -> None:
        from sme_gend.config.sme_context import load_sme_context

        filt = self._GP_KPI_FILTER.get(kpi_id)
        mois = (mois_ym or load_sme_context().mois_actif or "").strip() or None
        self._pending_gain_perte_mois = mois
        self._pending_gain_perte_filter = filt
        self._go_page(PAGE_GAIN_PERTE)
        self._apply_pending_gain_perte_nav()

    def _apply_pending_gain_perte_nav(self) -> None:
        mois = self._pending_gain_perte_mois
        filt = self._pending_gain_perte_filter
        if mois is None and filt is None:
            return
        w = self._page_widget(PAGE_GAIN_PERTE, wait_build=True)
        if w is None or not hasattr(w, "apply_navigation"):
            return
        w.apply_navigation(mois_ym=mois, filter_type=filt)
        self._pending_gain_perte_mois = None
        self._pending_gain_perte_filter = None

        self._pending_gain_perte_mois = None
        self._pending_gain_perte_filter = None

    def _update_page_nav_buttons(self) -> None:
        if hasattr(self, "_context_banner"):
            self._context_banner.set_page_nav_enabled(
                back=bool(self._nav_back_stack),
                forward=bool(self._nav_forward_stack),
            )

    def _nav_go_back(self) -> None:
        if not self._nav_back_stack:
            return
        target = self._nav_back_stack[-1]
        if not (0 <= target < self._stack.count()) or not session_can_access_page(target):
            self._nav_back_stack.pop()
            self._update_page_nav_buttons()
            return
        current = self._stack.currentIndex()
        self._nav_back_stack.pop()
        self._nav_forward_stack.append(current)
        self._go_page(target, record_history=False)

    def _nav_go_forward(self) -> None:
        if not self._nav_forward_stack:
            return
        target = self._nav_forward_stack[-1]
        if not (0 <= target < self._stack.count()) or not session_can_access_page(target):
            self._nav_forward_stack.pop()
            self._update_page_nav_buttons()
            return
        current = self._stack.currentIndex()
        self._nav_forward_stack.pop()
        self._nav_back_stack.append(current)
        if len(self._nav_back_stack) > _MAX_PAGE_NAV_HISTORY:
            self._nav_back_stack.pop(0)
        self._go_page(target, record_history=False)

    def _apply_pending_avancement_nav(self) -> None:
        idx = self._stack.currentIndex()
        if not is_avancement_page(idx):
            return
        view = self._avancement_view
        if view is None:
            return
        lookup = self._pending_avancement_lookup
        if lookup:
            view.focus_agent_lookup(lookup)
            self._pending_avancement_lookup = None

    def _go_page(
        self,
        index: int,
        *,
        record_history: bool = True,
        avancement_lookup: str | None = None,
    ) -> None:
        requested = index
        if index == PAGE_PILOTAGE_MOIS:
            index = PAGE_ARCHIVES_CLOSURE
        if not (0 <= index < self._stack.count()):
            return
        if index != PAGE_LISTE_GENERALE:
            self._pending_liste_structure_filter = None
            self._pending_liste_search = None
        if avancement_lookup and is_avancement_page(index):
            self._pending_avancement_lookup = (avancement_lookup or "").strip() or None
        if not session_can_access_page(index):
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                "Accès refusé",
                "Votre compte n'a pas la permission d'ouvrir cette section.",
            )
            return
        current = self._stack.currentIndex()
        if record_history and current != index:
            self._nav_back_stack.append(current)
            if len(self._nav_back_stack) > _MAX_PAGE_NAV_HISTORY:
                self._nav_back_stack.pop(0)
            self._nav_forward_stack.clear()
        host = self._stack.widget(index)
        label = self._nav_labels[index] if 0 <= index < len(self._nav_labels) else ""
        self._stack.setCurrentIndex(index)
        it = self._page_to_item.get(index)
        if it is not None:
            self._nav.setCurrentItem(it)
            self._nav.scrollToItem(it)
        if is_avancement_page(index):
            self._apply_pending_avancement_nav()
        if isinstance(host, LazyStackPage):
            if host.built_widget() is None:
                self.statusBar().showMessage(
                    f"Ouverture de « {label} »…" if label else "Chargement…",
                    0,
                )
                host.built.connect(
                    lambda child, idx=index: self._on_lazy_page_built(idx, child),
                    Qt.ConnectionType.SingleShotConnection,
                )
            host.ensure_built()
        else:
            self.statusBar().showMessage("Prêt", 2000)
        if requested == PAGE_PILOTAGE_MOIS:
            self._cycle_archives_view.show_cycle_tab()
        elif requested == PAGE_ARCHIVES_CLOSURE:
            self._cycle_archives_view.show_archives_tab()
        if index == PAGE_EDITION_SME:
            host_widget = self._page_widget(index, wait_build=False)
            if host_widget is not None and hasattr(host_widget, "on_page_activated"):
                host_widget.on_page_activated()
        self._update_page_nav_buttons()

    def _set_nav_compact_labels(self, compact: bool) -> None:
        align = (
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            if compact
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for item in self._walk_nav_items():
            label = item.data(0, ROLE_LABEL)
            if label is None:
                continue
            text = str(label)
            if compact:
                item.setText(0, "")
                item.setToolTip(0, text)
            else:
                item.setText(0, text)
                item.setToolTip(0, text)
            item.setTextAlignment(0, int(align))

    def _set_sidebar_compact(self, compact: bool) -> None:
        self._sidebar_compact = compact
        self._sidebar_frame.setProperty("compact", compact)
        self._sidebar_frame.style().unpolish(self._sidebar_frame)
        self._sidebar_frame.style().polish(self._sidebar_frame)

        if compact:
            self._sidebar_frame.setMinimumWidth(84)
            self._sidebar_frame.setMaximumWidth(100)
            self._period_panel.hide()
            self._btn_close_month_compact.show()
            self._btn_search_sidebar.setText("⌕")
            self._btn_logout_sidebar.setText("⎋")
            self._header_stack.setCurrentIndex(1)
            self._nav.setIconSize(QSize(26, 26))
            # Icônes alignées : pas d'indentation de branche (évite décalage + chevauchement scrollbar)
            self._nav.setIndentation(0)
            self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._set_nav_compact_labels(True)
            self._side_lay.setContentsMargins(6, 10, 6, 10)
        else:
            self._sidebar_frame.setMinimumWidth(248)
            self._sidebar_frame.setMaximumWidth(300)
            self._period_panel.show()
            self._btn_close_month_compact.hide()
            self._btn_search_sidebar.setText("Recherche")
            self._btn_logout_sidebar.setText("Se déconnecter")
            self._header_stack.setCurrentIndex(0)
            self._nav.setIconSize(QSize(22, 22))
            self._nav.setIndentation(14)
            self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._set_nav_compact_labels(False)
            self._side_lay.setContentsMargins(12, 20, 12, 16)

    def _collapse_sidebar(self) -> None:
        self._set_sidebar_compact(True)

    def _expand_sidebar(self) -> None:
        self._set_sidebar_compact(False)

    def _toggle_sidebar(self) -> None:
        self._set_sidebar_compact(not self._sidebar_compact)

    def _about(self) -> None:
        from sme_gend.ui.theme import theme_palette

        pal = theme_palette()
        # QDialog plutôt que QMessageBox(RichText) : certains thèmes Windows / Fusion
        # n'affichent pas correctement la boîte « À propos » (fenêtre invisible ou bloquée).
        dlg = QDialog(self)
        dlg.setWindowTitle(f"À propos — {APP_NAME}")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        body = QLabel(
            f"<h3 style=\"margin-top:0;\">{APP_NAME_FULL}</h3>"
            f"<p>{SME_FULL} — gestion et export des listes de personnel.</p>"
            f"<p><b>Version</b> {__version__}</p>"
            f"<p><b>Réalisation</b> : "
            f"<a href=\"https://smeth-dev.site\">smeth-dev.site</a></p>"
            f"<p style=\"color:{pal['text_muted']};\">Client desktop PySide6 — données opérationnelles MySQL.</p>"
            f"<p style=\"color:{pal['text_dim']};font-size:11px;\">Raccourcis : F1, Ctrl+H</p>"
        )
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay = QVBoxLayout(dlg)
        lay.addWidget(body)
        lay.addWidget(bb)
        dlg.adjustSize()
        dlg.exec()

    def _open_global_search(self) -> None:
        from sme_gend.models.personnel_lookup import PersonnelSearchHit
        from sme_gend.ui.global_search_dialog import GlobalSearchDialog
        from sme_gend.ui.historical_dossier_open import open_dossier_from_search_hit

        def _navigate(term: str) -> None:
            self._pending_liste_search = (term or "").strip() or None
            self._go_page(PAGE_LISTE_GENERALE)
            plv = self._personnel_list_view
            if plv is not None:
                plv._search.setText(term)
                plv._apply_filter()
                plv._search.setFocus()
                self._pending_liste_search = None

        def _open_hit(hit: PersonnelSearchHit) -> None:
            open_dossier_from_search_hit(
                self,
                self._settings,
                hit,
                navigate_active_fn=_navigate,
            )

        if self._global_search_dlg is None or not self._global_search_dlg.isVisible():
            self._global_search_dlg = GlobalSearchDialog(
                self._settings,
                _navigate,
                open_hit_fn=_open_hit,
                parent=self,
            )
        self._global_search_dlg.show_and_focus()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._has_unsaved_changes():
            text = (
                "Des modifications non enregistrées seront perdues.\n"
                f"Fermer l'application {APP_NAME} ?"
            )
        else:
            text = f"Fermer l'application {APP_NAME} ?"
        if not getattr(self, "_close_confirmed", False) and not confirm_yes_no(
            self,
            "Quitter",
            text,
            default_no=True,
        ):
            event.ignore()
            return
        self._close_confirmed = True
        self._shared_revision_timer.stop()
        worker = getattr(self, "_shared_revision_worker", None)
        if worker is not None:
            try:
                if worker.isRunning():
                    event.ignore()
                    if not getattr(self, "_waiting_close_sync", False):
                        self._waiting_close_sync = True
                        worker.finished.connect(self.close)
                    self.statusBar().showMessage("Fermeture après la synchronisation en cours…")
                    return
            except RuntimeError:
                pass  # Le worker terminé a déjà été libéré par deleteLater().
        from sme_gend.services.auth_context import get_app_auth

        auth = get_app_auth()
        if auth is not None:
            try:
                auth.logout()
            except Exception:
                _LOG.exception("Échec du nettoyage de la session lors de la fermeture.")
        try:
            from sme_gend.repositories.mysql_pool import invalidate_mysql_pool

            invalidate_mysql_pool(self._settings)
        except Exception:
            _LOG.exception("Échec de fermeture du pool MySQL lors de la fermeture.")
        super().closeEvent(event)

    def _has_unsaved_changes(self) -> bool:
        """Détecte les écrans dont l'indicateur `_dirty` signifie vraiment « saisie non
        enregistrée » (pas simplement « à recharger ») — cf. limite_age_view.py et
        detaches_config_view.py. Ne force pas la construction des pages différées."""
        for i in range(self._stack.count()):
            w = self._page_widget(i)
            check = getattr(w, "has_unsaved_changes", None)
            if callable(check) and check():
                return True
            if isinstance(w, (LimiteAgeView, DetachesConfigView)) and w.is_dirty():
                return True
        return False
