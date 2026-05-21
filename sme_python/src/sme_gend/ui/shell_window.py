"""Fenêtre principale — menu, sidebar brandée, navigation arborescente."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
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
from sme_gend.config import get_settings
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_preview_service import DetachesPreviewService
from sme_gend.services.detaches_service import STRUCTURE_CODE_DETACHE
from sme_gend.services.numerique_preview_service import NumeriquePreviewService
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.resources import icon_path
from sme_gend.ui.views.detaches_config_view import DetachesConfigView
from sme_gend.ui.views.detaches_view import DetachesView
from sme_gend.ui.views.home_view import HomeView
from sme_gend.ui.views.layout_editor_view import LayoutEditorView
from sme_gend.ui.views.liste_present_view import ListePresentView
from sme_gend.ui.views.numerique_combined_view import NumeriqueCombinedView
from sme_gend.ui.views.personnel_list_view import PersonnelListView
from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, PadminView, StructuresView
from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView
from sme_gend.ui.navigation_pages import (
    PAGE_COUNT,
    PAGE_CSV_IMPORT,
    PAGE_DASHBOARD,
    PAGE_ARCHIVES_CLOSURE,
    PAGE_DETACHES,
    PAGE_DETACHES_CONFIG,
    PAGE_DOC_GEN_LOG,
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
    PAGE_STRUCTURES_HORS_DETACHE,
)
from sme_gend.ui.views.archives_view import ArchivesView
from sme_gend.ui.views.csv_import_view import CsvImportView
from sme_gend.ui.views.doc_gen_log_view import DocGenLogView
from sme_gend.ui.views.edition_sme_view import EditionSmeView
from sme_gend.ui.views.gain_perte_view import GainPerteView
from sme_gend.ui.views.malade_punis_view import MaladePunisView
from sme_gend.ui.views.limite_age_view import LimiteAgeView
from sme_gend.ui.views.settings_view import SettingsView
from sme_gend.ui.views.soit_transmis_view import SoitTransmisView

# Données utilisateur sur QTreeWidgetItem : indice pile (-1 = dossier non navigable)
_ROLE_STACK = Qt.ItemDataRole.UserRole
_ROLE_LABEL = Qt.ItemDataRole.UserRole + 1

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


_PERIOD_SOURCE_STYLE_BASE = (
    "color: #1b5e20; font-weight: 600; background: #e8f5e9; "
    "padding: 3px 10px; border-radius: 4px; border: 1px solid #a5d6a7;"
)
_PERIOD_SOURCE_STYLE_ARCHIVE = (
    "color: #bf360c; font-weight: 700; background: #fff3e0; "
    "padding: 3px 10px; border-radius: 4px; border: 1px solid #ffb300;"
)
_PERIOD_SOURCE_STYLE_NEUTRAL = (
    "color: #5d4037; font-weight: 500; background: #fafafa; "
    "padding: 3px 10px; border-radius: 4px; border: 1px solid #e0e0e0;"
)


def _std_icon(style: QStyle, sp: QStyle.StandardPixmap) -> QIcon:
    return style.standardIcon(sp)


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
    ) -> None:
        super().__init__()
        self._personnel = personnel
        self._referentiel = referentiel
        self._liste_present = liste_present
        self._layout_cfg = layout_cfg
        self._dashboard = dashboard
        self._numerique_preview = numerique_preview
        self._detaches_preview = detaches_preview
        self._detaches_cfg = detaches_cfg

        self.setWindowTitle("SME — Situation mensuelle des effectifs")
        self.resize(1400, 820)
        self.setMinimumSize(1024, 680)

        style = self.style()
        self._page_to_item: dict[int, QTreeWidgetItem] = {}
        self._nav_labels: list[str] = []
        self._sidebar_compact = False

        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")

        def register_page(text: str, widget: QWidget) -> None:
            self._stack.addWidget(widget)
            self._nav_labels.append(text)

        # --- Pages (ordre pile = index utilisé partout ; libellés = navigation latérale + menu) ---
        self._home_view = HomeView(settings, dashboard, navigate_to_page=self._go_page)
        register_page("Synthèse & pilotage", self._home_view)
        personnel_list = PersonnelListView(personnel, referentiel, settings)
        self._personnel_list_view = personnel_list
        register_page("Liste générale", personnel_list)

        def _open_liste_for_structure(structure_code: str) -> None:
            self._go_page(PAGE_LISTE_GENERALE)
            personnel_list.apply_structure_filter(structure_code)

        register_page(
            "Effectifs par structure",
            StructureEffectifsView(
                settings,
                personnel,
                referentiel,
                _open_liste_for_structure,
            ),
        )
        _intro_hors_det = (
            "<b>Structures hors détachés</b> — mêmes informations que « Effectifs par structure », "
            "sans le code structure <b>DETACHE</b> (effectifs détachés : voir le dossier "
            "<b>Détachés</b> de la barre latérale). Double-clic ou bouton : "
            "<b>liste générale</b> filtrée sur la structure choisie."
        )
        register_page(
            "Structures (hors détachés)",
            StructureEffectifsView(
                settings,
                personnel,
                referentiel,
                _open_liste_for_structure,
                exclude_structure_codes=frozenset({STRUCTURE_CODE_DETACHE}),
                intro_html=_intro_hors_det,
            ),
        )
        register_page(
            "Mise en page (liste présents)",
            LayoutEditorView(layout_cfg, detaches_cfg),
        )
        register_page("Liste des présents", ListePresentView(liste_present, layout_cfg))
        register_page("Détachés", DetachesView(settings, detaches_preview, detaches_cfg))
        register_page("Structures (codes)", StructuresView(referentiel))
        register_page("Grades", GradesView(referentiel))
        register_page("Fonctions", FonctionsView(referentiel))
        register_page("Fichier Excel détachés", DetachesConfigView(detaches_cfg))
        register_page(
            "Situation numérique",
            NumeriqueCombinedView(settings, numerique_preview),
        )
        register_page("Archives de clôture", ArchivesView())
        register_page("Paramètres & données", SettingsView(settings))
        register_page("Gains & Pertes", GainPerteView(settings))
        register_page("Malades & Punis", MaladePunisView(settings))
        self._limite_age_view = LimiteAgeView(settings)
        register_page("Limite d'âge", self._limite_age_view)

        self._soit_transmis_view = SoitTransmisView(settings)
        register_page("Soit transmis", self._soit_transmis_view)

        self._csv_import_view = CsvImportView(settings)
        register_page("Import CSV / Excel", self._csv_import_view)

        register_page("Historique documents", DocGenLogView())

        self._edition_sme_view = EditionSmeView(settings)
        register_page("Edition SME", self._edition_sme_view)

        register_page("Positions administratives", PadminView(referentiel))

        # Partager l'instance LimiteAgeService entre les deux vues (Point 17)
        self._soit_transmis_view.set_limite_age_service(self._limite_age_view.limite_age_service)

        # Lier le bouton "→ Soit transmis" de l'onglet Attestations
        def _push_to_soit_transmis(persons, grade_code_map=None, results=None):
            self._soit_transmis_view.load_persons(persons, grade_code_map, results)
            self._go_page(PAGE_SOIT_TRANSMIS)

        self._limite_age_view.set_send_to_soit_transmis_fn(_push_to_soit_transmis)

        if self._stack.count() != PAGE_COUNT:
            raise RuntimeError(
                f"navigation_pages.PAGE_COUNT={PAGE_COUNT} != pile={self._stack.count()} "
                "(mettre à jour navigation_pages si l'ordre des pages change)."
            )

        # Icônes réelles (register_page a mis des QIcon() vides pour index 0)
        icons: list[QIcon] = [
            _std_icon(style, QStyle.StandardPixmap.SP_DirHomeIcon),         # 0 DASHBOARD
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogListView),  # 1 LISTE_GENERALE
            _std_icon(style, QStyle.StandardPixmap.SP_ComputerIcon),        # 2 STRUCTURES_EFFECTIFS
            _std_icon(style, QStyle.StandardPixmap.SP_ComputerIcon),      # 3 STRUCTURES_HORS_DETACHE
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogDetailedView),  # 4 LAYOUT_EDITOR
            _std_icon(style, QStyle.StandardPixmap.SP_DialogApplyButton),   # 5 LISTE_PRESENTS
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogContentsView),  # 6 DETACHES
            _std_icon(style, QStyle.StandardPixmap.SP_DesktopIcon),         # 7 STRUCTURES
            _std_icon(style, QStyle.StandardPixmap.SP_MessageBoxInformation),  # 8 GRADES
            _std_icon(style, getattr(QStyle.StandardPixmap, "SP_UserIcon",
                QStyle.StandardPixmap.SP_FileDialogContentsView)),           # 9 FONCTIONS
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogInfoView),  # 10 DETACHES_CONFIG
            _std_icon(style, QStyle.StandardPixmap.SP_ComputerIcon),        # 11 NUMERIQUE
            _std_icon(style, QStyle.StandardPixmap.SP_DirLinkIcon),         # 12 ARCHIVES_CLOSURE
            _std_icon(style, getattr(QStyle.StandardPixmap, "SP_FileDialogDetailedView",
                QStyle.StandardPixmap.SP_FileDialogInfoView)),               # 13 SETTINGS
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogContentsView),  # 14 GAIN_PERTE
            _std_icon(style, QStyle.StandardPixmap.SP_MessageBoxWarning),     # 15 MALADE_PUNIS
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogInfoView),   # 16 LIMITE_AGE
            _std_icon(style, QStyle.StandardPixmap.SP_FileLinkIcon),          # 17 SOIT_TRANSMIS
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogContentsView),# 18 CSV_IMPORT
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogListView),    # 19 DOC_GEN_LOG
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogDetailedView), # 20 EDITION_SME
            _std_icon(style, QStyle.StandardPixmap.SP_MessageBoxInformation),   # 21 PADMIN
        ]
        if len(icons) != PAGE_COUNT:
            raise RuntimeError(
                f"Incohérence icônes/pages : {len(icons)} icônes pour PAGE_COUNT={PAGE_COUNT}. "
                "Mettre à jour la liste icons[] lors de tout ajout de page."
            )

        self._nav = QTreeWidget()
        self._nav.setObjectName("navTree")
        self._nav.setHeaderHidden(True)
        self._nav.setRootIsDecorated(True)
        self._nav.setIndentation(14)
        self._nav.setAnimated(True)
        self._nav.setIconSize(QSize(22, 22))
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setUniformRowHeights(True)
        self._nav.setWordWrap(False)
        self._nav.setTextElideMode(Qt.TextElideMode.ElideNone)

        def add_leaf(parent: QTreeWidgetItem | None, label: str, stack_idx: int) -> QTreeWidgetItem:
            if parent is None:
                it = QTreeWidgetItem(self._nav, [label])
            else:
                it = QTreeWidgetItem(parent, [label])
            it.setIcon(0, icons[stack_idx])
            it.setData(0, _ROLE_STACK, stack_idx)
            it.setData(0, _ROLE_LABEL, label)
            it.setToolTip(0, label)
            it.setTextAlignment(0, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            self._page_to_item[stack_idx] = it
            return it

        def add_folder(text: str, folder_icon: QIcon) -> QTreeWidgetItem:
            it = QTreeWidgetItem(self._nav, [text])
            it.setIcon(0, folder_icon)
            it.setData(0, _ROLE_STACK, -1)
            it.setData(0, _ROLE_LABEL, text)
            it.setToolTip(0, text)
            it.setExpanded(True)
            it.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            return it

        nl = self._nav_labels
        add_leaf(None, nl[PAGE_DASHBOARD], PAGE_DASHBOARD)

        f_eff = add_folder(
            "Effectifs du mois",
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogNewFolder),
        )
        add_leaf(f_eff, nl[PAGE_LISTE_GENERALE], PAGE_LISTE_GENERALE)
        add_leaf(f_eff, nl[PAGE_EDITION_SME], PAGE_EDITION_SME)
        add_leaf(f_eff, nl[PAGE_STRUCTURES_EFFECTIFS], PAGE_STRUCTURES_EFFECTIFS)
        add_leaf(f_eff, nl[PAGE_STRUCTURES_HORS_DETACHE], PAGE_STRUCTURES_HORS_DETACHE)
        add_leaf(f_eff, nl[PAGE_LISTE_PRESENTS], PAGE_LISTE_PRESENTS)
        add_leaf(f_eff, nl[PAGE_LAYOUT_EDITOR], PAGE_LAYOUT_EDITOR)
        add_leaf(f_eff, nl[PAGE_NUMERIQUE], PAGE_NUMERIQUE)
        add_leaf(f_eff, nl[PAGE_GAIN_PERTE], PAGE_GAIN_PERTE)
        add_leaf(f_eff, nl[PAGE_MALADE_PUNIS], PAGE_MALADE_PUNIS)

        f_attest = add_folder(
            "Attestations",
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogInfoView),
        )
        add_leaf(f_attest, nl[PAGE_LIMITE_AGE],    PAGE_LIMITE_AGE)
        add_leaf(f_attest, nl[PAGE_SOIT_TRANSMIS], PAGE_SOIT_TRANSMIS)

        f_det = add_folder(
            "Détachés",
            _std_icon(
                style,
                getattr(QStyle.StandardPixmap, "SP_DriveNetIcon", QStyle.StandardPixmap.SP_DirIcon),
            ),
        )
        add_leaf(f_det, nl[PAGE_DETACHES], PAGE_DETACHES)
        add_leaf(f_det, nl[PAGE_DETACHES_CONFIG], PAGE_DETACHES_CONFIG)

        f_ref = add_folder(
            "Référentiels",
            _std_icon(style, QStyle.StandardPixmap.SP_DialogOpenButton),
        )
        add_leaf(f_ref, nl[PAGE_STRUCTURES], PAGE_STRUCTURES)
        add_leaf(f_ref, nl[PAGE_GRADES], PAGE_GRADES)
        add_leaf(f_ref, nl[PAGE_FONCTIONS], PAGE_FONCTIONS)
        add_leaf(f_ref, nl[PAGE_PADMIN], PAGE_PADMIN)

        f_admin = add_folder(
            "Administration",
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogDetailedView),
        )
        add_leaf(f_admin, nl[PAGE_SETTINGS], PAGE_SETTINGS)
        add_leaf(f_admin, nl[PAGE_ARCHIVES_CLOSURE], PAGE_ARCHIVES_CLOSURE)
        add_leaf(f_admin, nl[PAGE_CSV_IMPORT], PAGE_CSV_IMPORT)
        add_leaf(f_admin, nl[PAGE_DOC_GEN_LOG], PAGE_DOC_GEN_LOG)

        self._nav.itemClicked.connect(self._on_nav_item_clicked)
        self._nav.setCurrentItem(self._page_to_item[PAGE_DASHBOARD])
        self._stack.setCurrentIndex(PAGE_DASHBOARD)
        self._stack.currentChanged.connect(self._on_content_page_changed)

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
        logo_path = icon_path("app.svg")
        self._logo_lbl = QLabel()
        if logo_path.is_file():
            self._logo_lbl.setPixmap(QIcon(str(logo_path)).pixmap(52, 52))
        self._logo_lbl.setFixedSize(56, 56)

        self._sidebar_brand = QLabel("GENDARMERIE NATIONALE")
        self._sidebar_brand.setObjectName("sidebarBrand")
        self._sidebar_title = QLabel("SME")
        self._sidebar_title.setObjectName("sidebarTitle")
        self._sidebar_sub = QLabel("Effectifs")
        self._sidebar_sub.setStyleSheet("color: #b0bec5; font-size: 12px;")

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
        self._head_expanded.setLayout(head_row)

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
        if logo_path.is_file():
            self._logo_small.setPixmap(QIcon(str(logo_path)).pixmap(28, 28))
        self._logo_small.setFixedSize(32, 32)
        self._logo_small.setAlignment(Qt.AlignmentFlag.AlignCenter)

        head_narrow_lay = QVBoxLayout()
        head_narrow_lay.setSpacing(8)
        head_narrow_lay.setContentsMargins(4, 8, 4, 8)
        head_narrow_lay.addWidget(self._btn_expand_sidebar)
        head_narrow_lay.addWidget(self._logo_small, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._head_compact = QWidget()
        self._head_compact.setLayout(head_narrow_lay)
        self._head_compact.hide()

        self._header_stack = QStackedWidget()
        self._header_stack.addWidget(self._head_expanded)
        self._header_stack.addWidget(self._head_compact)

        self._sidebar_frame = QFrame()
        self._sidebar_frame.setObjectName("sidebarFrame")
        self._sidebar_frame.setMinimumWidth(260)
        self._sidebar_frame.setMaximumWidth(280)
        self._side_lay = QVBoxLayout(self._sidebar_frame)
        self._side_lay.setContentsMargins(12, 20, 12, 16)
        self._side_lay.setSpacing(8)
        self._side_lay.addWidget(self._header_stack)
        self._side_lay.addWidget(self._nav, stretch=1)

        self._btn_help_sidebar = QToolButton()
        self._btn_help_sidebar.setObjectName("sidebarHelpBtn")
        self._btn_help_sidebar.setText("?")
        self._btn_help_sidebar.setToolTip(
            "À propos / aide — F1 ou Ctrl+H · Recherche globale — Ctrl+K"
        )
        self._btn_help_sidebar.setAutoRaise(True)
        self._btn_help_sidebar.setMinimumSize(36, 36)
        self._btn_help_sidebar.clicked.connect(self._about)
        help_row = QHBoxLayout()
        help_row.addStretch()
        help_row.addWidget(self._btn_help_sidebar)
        help_row.addStretch()
        self._side_lay.addLayout(help_row)

        self._period_banner = QFrame()
        self._period_banner.setObjectName("appPeriodBanner")
        pb_lay = QHBoxLayout(self._period_banner)
        pb_lay.setContentsMargins(14, 6, 14, 6)
        pb_lay.setSpacing(8)

        lbl_sme = QLabel("<b>SME ·</b>")
        lbl_sme.setObjectName("appPeriodBannerLabel")

        self._btn_prev_month = QToolButton()
        self._btn_prev_month.setText("◄")
        self._btn_prev_month.setObjectName("periodNavBtn")
        self._btn_prev_month.setToolTip("Mois précédent")
        self._btn_prev_month.setAutoRaise(True)
        self._btn_prev_month.clicked.connect(self._go_prev_month)

        self._period_month_lbl = QLabel("—")
        self._period_month_lbl.setObjectName("periodMonthLabel")
        self._period_month_lbl.setMinimumWidth(130)
        self._period_month_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._period_statut_lbl = QLabel()
        self._period_statut_lbl.setObjectName("periodStatutLabel")

        self._btn_next_month = QToolButton()
        self._btn_next_month.setText("►")
        self._btn_next_month.setObjectName("periodNavBtn")
        self._btn_next_month.setToolTip("Mois suivant")
        self._btn_next_month.setAutoRaise(True)
        self._btn_next_month.clicked.connect(self._go_next_month)

        btn_period = QPushButton("Tableau de bord…")
        btn_period.setObjectName("appPeriodBannerBtn")
        btn_period.setToolTip("Synthèse, clôture, réouverture exceptionnelle.")
        btn_period.clicked.connect(lambda: self._go_page(PAGE_DASHBOARD))

        btn_months_overview = QPushButton("Changer de période…")
        btn_months_overview.setObjectName("appPeriodBannerBtn")
        btn_months_overview.setToolTip(
            "Ouvre le calendrier des mois (TabSme, archives). Double-cliquez un mois pour l'activer."
        )
        btn_months_overview.clicked.connect(self._on_months_overview_dialog)

        pb_lay.addWidget(lbl_sme)
        pb_lay.addWidget(self._btn_prev_month)
        pb_lay.addWidget(self._period_month_lbl)
        pb_lay.addWidget(self._period_statut_lbl)
        pb_lay.addWidget(self._btn_next_month)

        self._period_source_lbl = QLabel()
        self._period_source_lbl.setObjectName("periodSourceLabel")
        self._period_source_lbl.setMinimumWidth(140)
        pb_lay.addWidget(self._period_source_lbl)

        pb_lay.addStretch()
        pb_lay.addWidget(btn_months_overview, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        pb_lay.addWidget(btn_period, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        content_row = QWidget()
        row_lay = QHBoxLayout(content_row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(0)
        row_lay.addWidget(self._sidebar_frame)
        row_lay.addWidget(self._stack, stretch=1)

        central = QWidget()
        vcentral = QVBoxLayout(central)
        vcentral.setContentsMargins(0, 0, 0, 0)
        vcentral.setSpacing(0)
        vcentral.addWidget(self._period_banner)
        vcentral.addWidget(content_row, stretch=1)
        self.setCentralWidget(central)

        self._settings = settings
        # Rebind services sur la bonne source dès le démarrage
        # (si mois_actif est fermé avec archive, les vues doivent voir l'archive)
        self.refresh_for_month_change()
        self._build_menu_bar()

        status = QStatusBar()
        self.setStatusBar(status)
        status.showMessage("Prêt")
        self._status_data_lbl = QLabel()
        self._refresh_data_status_label()
        status.addPermanentWidget(self._status_data_lbl)

        _layout_editor_ref: LayoutEditorView | None = None
        _liste_present_ref: ListePresentView | None = None
        for i in range(self._stack.count()):
            w = self._stack.widget(i)
            if isinstance(w, ListePresentView):
                w.set_open_layout_editor(lambda: self._go_page(PAGE_LAYOUT_EDITOR))
                _liste_present_ref = w
            elif isinstance(w, LayoutEditorView):
                _layout_editor_ref = w
        if _layout_editor_ref is not None and _liste_present_ref is not None:
            _layout_editor_ref.set_on_saved_callback(
                _liste_present_ref.apply_runtime_after_config_change
            )

    def _on_nav_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        del column
        idx = item.data(0, _ROLE_STACK)
        if idx is None:
            return
        try:
            stack_idx = int(idx)
        except (TypeError, ValueError):
            return
        if stack_idx < 0:
            return
        self._stack.setCurrentIndex(stack_idx)

    def _on_content_page_changed(self, index: int) -> None:
        self._refresh_period_banner()
        # Edition SME : chargement dans EditionSmeView.showEvent (première ouverture + changement de mois).

    def _go_prev_month(self) -> None:
        self._navigate_month(-1)

    def _go_next_month(self) -> None:
        self._navigate_month(+1)

    def _navigate_month(self, direction: int) -> None:
        from sme_gend.repositories.sme_repository import SmeRepository
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

        ctx = load_sme_context()
        current = (canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or "").strip()

        mois_list: list[str] = []
        try:
            repo = SmeRepository(self._settings)
            if repo.table_exists():
                seen: set[str] = set()
                for r in repo.fetch_all():
                    ym = canonical_sme_mois_yyyy_mm(r.mois)
                    if ym and ym not in seen:
                        seen.add(ym)
                        mois_list.append(ym)
                mois_list.sort()
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
            ctx.mois_actif = new_mois
            save_sme_context(ctx)
            self.refresh_for_month_change()

    def _refresh_active_views_on_month_change(self) -> None:
        from sme_gend.ui.views.archives_view import ArchivesView

        new_mois = load_sme_context().mois_actif or None
        for i in range(self._stack.count()):
            w = self._stack.widget(i)
            if isinstance(w, ArchivesView):
                w.set_active_mois(new_mois)
            if isinstance(w, HomeView):
                w._sme_panel.refresh_workflow()
            # Marque toutes les vues "dirty" : rechargement depuis la bonne source au prochain affichage
            if hasattr(w, "mark_dirty"):
                w.mark_dirty()

        # Rafraîchit la page courante immédiatement
        current = self._stack.widget(self._stack.currentIndex())
        if hasattr(current, "_reload_stats"):
            current._reload_stats(silent=True)
        elif hasattr(current, "apply_runtime_after_config_change"):
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
        self._limite_age_view.rebind_settings(data_settings)
        self._soit_transmis_view.rebind_settings(data_settings)
        # Référentiel : rebind sur data_settings pour lire grades/structures de l'archive.
        # En mode live, data_settings == self._settings (pas d'impact).
        self._referentiel.rebind_settings(data_settings)
        self._edition_sme_view.rebind_settings(data_settings)

    def _apply_month_data_source(self, mois: str) -> None:
        """
        Quand on navigue vers un mois fermé avec une archive, rebinde les services
        sur ce fichier archive. Sinon, restaure les services sur la base principale.
        """
        import dataclasses
        from sme_gend.config.sme_context import load_sme_context as _ctx
        from sme_gend.services.sme_month_closure import find_archive_for_month

        ctx = _ctx()
        is_closed = mois in (ctx.mois_fermes or [])
        archive_path = find_archive_for_month(mois) if is_closed else None

        if archive_path is not None:
            ext = archive_path.suffix.lower()
            if ext in (".sqlite", ".db"):
                arc_s = dataclasses.replace(
                    self._settings,
                    sqlite_liste_path=archive_path,
                    use_sqlite_liste_generale=True,
                )
            else:
                arc_s = dataclasses.replace(
                    self._settings,
                    database_path=archive_path,
                    use_sqlite_liste_generale=False,
                )
            self._rebind_data_services(arc_s, ref_settings=self._settings)
            self._period_source_lbl.setText(f"Archive · lecture seule ({mois})")
            self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_ARCHIVE)
        else:
            self._rebind_data_services(self._settings)
            self._period_source_lbl.setText("Base de travail")
            self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_BASE)

    def _on_months_overview_dialog(self) -> None:
        """Affiche le calendrier des mois : statut TabSme + présence d'archive pour chacun."""
        from sme_gend.repositories.sme_repository import SmeRepository
        from sme_gend.services.sme_month_closure import (
            canonical_sme_mois_yyyy_mm,
            find_archive_for_month,
        )
        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
            QDialogButtonBox, QHeaderView,
        )
        from PySide6.QtGui import QColor

        ctx = load_sme_context()
        mois_actif = (ctx.mois_actif or "").strip()

        # --- Collecte des mois connus ---
        # 1. depuis TabSme
        tabsme_map: dict[str, str] = {}  # YYYY-MM → statut
        try:
            repo = SmeRepository(self._settings)
            if repo.table_exists():
                for r in repo.fetch_all():
                    ym = canonical_sme_mois_yyyy_mm(r.mois)
                    if ym:
                        tabsme_map[ym] = r.statut or "—"
        except Exception:
            pass

        # 2. depuis mois_fermes (contexte local)
        all_months: set[str] = set(tabsme_map.keys())
        for m in (ctx.mois_fermes or []):
            ym = canonical_sme_mois_yyyy_mm(m)
            if ym:
                all_months.add(ym)
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

        info = QLabel(
            "Liste des mois connus (TabSme + historique local). "
            "<b>Archive</b> = fichier de données importé dans <code>sme_archives/</code>. "
            "Double-cliquez sur un mois pour y naviguer directement."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(info)

        tbl = QTableWidget(len(sorted_months), 4)
        tbl.setHorizontalHeaderLabels(["Mois", "Statut TabSme", "Archive", "État"])
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

        for row, ym in enumerate(sorted_months):
            archive = find_archive_for_month(ym)
            statut = tabsme_map.get(ym, "—")
            is_ferme = ym in (ctx.mois_fermes or [])
            is_actif = ym == canonical_sme_mois_yyyy_mm(mois_actif)

            if is_actif:
                etat = "● Actif"
                row_color = QColor("#e8f5e9")
            elif archive is not None:
                etat = "✓ Archive disponible"
                row_color = QColor("#e3f2fd")
            elif is_ferme:
                etat = "○ Clôturé, sans archive"
                row_color = QColor("#fff8e1")
            else:
                etat = "—"
                row_color = None

            tbl.setItem(row, 0, QTableWidgetItem(ym))
            tbl.setItem(row, 1, QTableWidgetItem(statut))
            tbl.setItem(row, 2, QTableWidgetItem(
                f"✓  {archive.name}" if archive else "—"
            ))
            tbl.setItem(row, 3, QTableWidgetItem(etat))

            if row_color:
                for col in range(4):
                    item = tbl.item(row, col)
                    if item:
                        item.setBackground(row_color)

        def _on_double_click(r: int, _c: int) -> None:
            ym_sel = tbl.item(r, 0)
            if ym_sel is None:
                return
            target = ym_sel.text().strip()
            if not target:
                return
            ctx2 = load_sme_context()
            ctx2.mois_actif = target
            save_sme_context(ctx2)
            self.refresh_for_month_change()
            dlg.accept()

        tbl.cellDoubleClicked.connect(_on_double_click)
        lay.addWidget(tbl)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        dlg.exec()

    def refresh_for_month_change(self) -> None:
        """
        Rebinde les services sur la source correcte pour le mois actif
        (archive si fermé + archive disponible, sinon base live), puis
        rafraîchit le bandeau et marque toutes les vues comme dirty.
        À appeler au démarrage et après tout changement de période (import, clôture, navigation).
        """
        ctx = load_sme_context()
        mois = (ctx.mois_actif or "").strip()
        if mois:
            try:
                self._apply_month_data_source(mois)
            except Exception:
                pass  # ne pas bloquer si l'archive est inaccessible
        else:
            try:
                self._rebind_data_services(self._settings)
            except Exception:
                pass
            self._period_source_lbl.setText("Base — définir le mois actif")
            self._period_source_lbl.setStyleSheet(_PERIOD_SOURCE_STYLE_NEUTRAL)
        self._refresh_period_banner()
        self._refresh_active_views_on_month_change()

    def _refresh_period_banner(self) -> None:
        from sme_gend.services.sme_editing_policy import evaluate_editing_state

        ctx = load_sme_context()
        mois = (ctx.mois_actif or "").strip()
        self._period_month_lbl.setText(_format_mois_fr(mois))

        try:
            st = evaluate_editing_state(self._settings)
        except Exception:
            self._period_statut_lbl.setText("⚠ indisponible")
            self._period_statut_lbl.setStyleSheet("color: #b71c1c;")
            return

        if st.locked:
            self._period_statut_lbl.setText(
                f"● {st.tabsme_statut or 'Fermé'} — saisie bloquée (TabSme)"
            )
            self._period_statut_lbl.setStyleSheet("color: #b71c1c; font-weight: 600;")
        elif st.tabsme_statut:
            self._period_statut_lbl.setText(f"● {st.tabsme_statut}")
            self._period_statut_lbl.setStyleSheet("color: #2e7d32; font-weight: 600;")
        elif not mois:
            self._period_statut_lbl.setText("non définie")
            self._period_statut_lbl.setStyleSheet("color: #6d4c41;")
        else:
            self._period_statut_lbl.setText("● saisie autorisée")
            self._period_statut_lbl.setStyleSheet("color: #1565c0;")

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
        act_paths = QAction("Chemins des fichiers de données…", self)
        act_paths.setToolTip(
            "SQLite (données principales), Access (import depuis l'ancien .mdb), "
            "modèles Excel, archives. Rechargement immédiat après enregistrement."
        )
        act_paths.triggered.connect(self._open_paths_config)
        m_file.addAction(act_paths)

        act_import = QAction("Importer une base existante…", self)
        act_import.setToolTip(
            "Importe un fichier .sqlite ou .mdb comme archive d'un mois passé : "
            "copie vers sme_archives/, TabSme mis à jour."
        )
        act_import.triggered.connect(lambda: self._home_view._sme_panel._on_sme_import_dialog())
        m_file.addAction(act_import)

        m_file.addSeparator()
        act_quit = QAction("Quitter", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        # ── Effectifs ─────────────────────────────────────────────────────────
        m_eff = bar.addMenu("&Effectifs")
        m_eff.addAction(_nav_action(PAGE_DASHBOARD))
        m_eff.addAction(_nav_action(PAGE_LISTE_GENERALE))
        m_eff.addAction(_nav_action(PAGE_EDITION_SME))
        m_eff.addAction(_nav_action(PAGE_STRUCTURES_EFFECTIFS))
        m_eff.addAction(_nav_action(PAGE_STRUCTURES_HORS_DETACHE))
        m_eff.addAction(_nav_action(PAGE_LISTE_PRESENTS))
        m_eff.addAction(_nav_action(PAGE_NUMERIQUE))
        m_eff.addAction(_nav_action(PAGE_GAIN_PERTE))
        m_eff.addAction(_nav_action(PAGE_MALADE_PUNIS))

        # ── Détachés ──────────────────────────────────────────────────────────
        m_det = bar.addMenu("&Détachés")
        m_det.addAction(_nav_action(PAGE_DETACHES))
        m_det.addAction(_nav_action(PAGE_DETACHES_CONFIG))

        # ── Référentiels ──────────────────────────────────────────────────────
        m_ref = bar.addMenu("&Référentiels")
        m_ref.addAction(_nav_action(PAGE_STRUCTURES))
        m_ref.addAction(_nav_action(PAGE_GRADES))
        m_ref.addAction(_nav_action(PAGE_FONCTIONS))
        m_ref.addAction(_nav_action(PAGE_PADMIN))

        # ── Administration ────────────────────────────────────────────────────
        m_adm = bar.addMenu("&Administration")
        m_adm.addAction(_nav_action(PAGE_ARCHIVES_CLOSURE))
        m_adm.addAction(_nav_action(PAGE_LAYOUT_EDITOR))
        m_adm.addAction(_nav_action(PAGE_SETTINGS))
        m_adm.addSeparator()
        m_adm.addAction(_nav_action(PAGE_CSV_IMPORT))
        m_adm.addAction(_nav_action(PAGE_DOC_GEN_LOG))

        # ── Affichage ─────────────────────────────────────────────────────────
        m_view = bar.addMenu("&Affichage")
        act_search = QAction("Recherche globale…", self)
        act_search.setShortcut("Ctrl+K")
        act_search.setToolTip(
            "Recherche transversale (personnel, navigation). Raccourci visible aussi sous le menu Aide."
        )
        act_search.triggered.connect(self._open_global_search)
        m_view.addAction(act_search)
        act_reload = QAction("Actualiser la synthèse", self)
        act_reload.setShortcut("F5")
        act_reload.triggered.connect(self._refresh_dashboard_if_visible)
        m_view.addAction(act_reload)
        act_sidebar = QAction("Réduire / développer la barre latérale", self)
        act_sidebar.setShortcut("Ctrl+B")
        act_sidebar.triggered.connect(self._toggle_sidebar)
        m_view.addAction(act_sidebar)

        m_view.addSeparator()
        m_theme = m_view.addMenu("Thème")
        ctx_theme = load_sme_context()
        self._act_theme_light = QAction("Clair", self)
        self._act_theme_light.setCheckable(True)
        self._act_theme_light.setChecked(ctx_theme.theme == "light")
        self._act_theme_dark = QAction("Sombre", self)
        self._act_theme_dark.setCheckable(True)
        self._act_theme_dark.setChecked(ctx_theme.theme == "dark")
        theme_grp = QActionGroup(self)
        theme_grp.setExclusive(True)
        theme_grp.addAction(self._act_theme_light)
        theme_grp.addAction(self._act_theme_dark)
        self._act_theme_light.triggered.connect(lambda: self._apply_theme("light"))
        self._act_theme_dark.triggered.connect(lambda: self._apply_theme("dark"))
        m_theme.addAction(self._act_theme_light)
        m_theme.addAction(self._act_theme_dark)

        # ── Aide ──────────────────────────────────────────────────────────────
        m_help = bar.addMenu("&Aide")
        m_help.addAction(act_search)
        m_help.addSeparator()
        act_about = QAction("À propos de SME…", self)
        act_about.setMenuRole(QAction.MenuRole.NoRole)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _open_paths_config(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from sme_gend.ui.config_paths_dialog import ConfigPathsDialog

        dlg = ConfigPathsDialog(self, self._settings)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.reload_runtime_configuration(show_success_dialog=True)

    def _apply_theme(self, theme: str) -> None:
        from sme_gend.ui.theme import apply_app_theme
        ctx = load_sme_context()
        ctx.theme = theme
        save_sme_context(ctx)
        apply_app_theme(QApplication.instance(), theme)
        self._act_theme_light.setChecked(theme == "light")
        self._act_theme_dark.setChecked(theme == "dark")

    def _refresh_data_status_label(self) -> None:
        s = self._settings
        if s.use_sqlite_liste_generale:
            sql = str(s.sqlite_liste_path) if s.sqlite_liste_path else "—"
            acc = str(s.database_path)
            txt = f"Données : SQLite — {sql} · Access (import) : {acc}"
        else:
            txt = f"Données : Access (exploitation) — {s.database_path}"
        self._status_data_lbl.setText(txt)

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
        self._detaches_cfg.rebind_settings(new_settings)
        self._layout_cfg.rebind_settings(new_settings)
        self._referentiel.rebind_settings(new_settings)
        self._personnel.rebind_settings(new_settings)
        self._liste_present.rebind_settings(new_settings)
        self._dashboard.rebind_settings(new_settings)
        self._numerique_preview.rebind_settings(new_settings)
        self._detaches_preview.rebind_settings(new_settings)
        self._settings = new_settings
        self._refresh_data_status_label()
        self._propagate_runtime_settings_to_views()
        self._refresh_period_banner()
        if show_success_dialog:
            QMessageBox.information(
                self,
                "Configuration appliquée",
                "Les chemins et le mode de données ont été rechargés. Les écrans utilisent "
                "désormais la nouvelle configuration.",
            )
        return True

    def _propagate_runtime_settings_to_views(self) -> None:
        from sme_gend.ui.views.detaches_view import DetachesView
        from sme_gend.ui.views.gain_perte_view import GainPerteView as _GainPerteView
        from sme_gend.ui.views.malade_punis_view import MaladePunisView as _MaladePunisView
        from sme_gend.ui.views.home_view import HomeView
        from sme_gend.ui.views.liste_present_view import ListePresentView
        from sme_gend.ui.views.numerique_combined_view import NumeriqueCombinedView as _NumeriqueCombinedView
        from sme_gend.ui.views.personnel_list_view import PersonnelListView
        from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, PadminView, StructuresView
        from sme_gend.ui.views.settings_view import SettingsView
        from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView

        s = self._settings
        for i in range(self._stack.count()):
            w = self._stack.widget(i)
            if isinstance(w, HomeView):
                w.apply_runtime_settings(s)
            elif isinstance(w, PersonnelListView):
                w.apply_runtime_after_config_change()
            elif isinstance(w, ListePresentView):
                w.apply_runtime_after_config_change()
            elif isinstance(w, DetachesView):
                w.apply_runtime_settings(s)
            elif isinstance(w, _NumeriqueCombinedView):
                w.apply_runtime_settings(s)
            elif isinstance(w, (StructuresView, GradesView, FonctionsView, PadminView)):
                w.apply_runtime_after_config_change()
            elif isinstance(w, StructureEffectifsView):
                w.apply_runtime_after_config_change()
            elif isinstance(w, SettingsView):
                w.apply_runtime_settings(s)
            elif isinstance(w, _GainPerteView):
                w.apply_runtime_settings(s)
            elif isinstance(w, _MaladePunisView):
                w.apply_runtime_settings(s)
            elif isinstance(w, CsvImportView):
                w.rebind_settings(s)
            elif isinstance(w, EditionSmeView):
                w.rebind_settings(s)

    def _refresh_dashboard_if_visible(self) -> None:
        if self._stack.currentIndex() != PAGE_DASHBOARD:
            return
        w = self._stack.widget(PAGE_DASHBOARD)
        if hasattr(w, "_reload_stats"):
            w._reload_stats()
            self.statusBar().showMessage("Indicateurs actualisés.", 3000)

    def _go_page(self, index: int) -> None:
        if 0 <= index < self._stack.count():
            self._stack.setCurrentIndex(index)
            it = self._page_to_item.get(index)
            if it is not None:
                self._nav.setCurrentItem(it)
                self._nav.scrollToItem(it)

    def _set_nav_compact_labels(self, compact: bool) -> None:
        align = (
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            if compact
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for item in self._walk_nav_items():
            label = item.data(0, _ROLE_LABEL)
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
            self._header_stack.setCurrentIndex(1)
            self._nav.setIconSize(QSize(26, 26))
            # Icônes alignées : pas d'indentation de branche (évite décalage + chevauchement scrollbar)
            self._nav.setIndentation(0)
            self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self._set_nav_compact_labels(True)
            self._side_lay.setContentsMargins(6, 10, 6, 10)
        else:
            self._sidebar_frame.setMinimumWidth(260)
            self._sidebar_frame.setMaximumWidth(300)
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
        # QDialog plutôt que QMessageBox(RichText) : certains thèmes Windows / Fusion
        # n'affichent pas correctement la boîte « À propos » (fenêtre invisible ou bloquée).
        dlg = QDialog(self)
        dlg.setWindowTitle("À propos — SME")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        body = QLabel(
            f"<h3 style=\"margin-top:0;\">SME — Situation mensuelle des effectifs</h3>"
            f"<p>Application de gestion et d'export des listes de personnel.</p>"
            f"<p><b>Version</b> {__version__}</p>"
            f"<p><b>Réalisation</b> : "
            f"<a href=\"https://smeth-dev.site\">smeth-dev.site</a></p>"
            f"<p style=\"color:#666;\">Client desktop PySide6 — données Access.</p>"
            f"<p style=\"color:#888;font-size:11px;\">Raccourcis : F1, Ctrl+H</p>"
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
        from sme_gend.ui.global_search_dialog import GlobalSearchDialog

        def _navigate(term: str) -> None:
            self._go_page(PAGE_LISTE_GENERALE)
            plv = self._personnel_list_view
            plv._search.setText(term)
            plv._search.setFocus()

        if self._global_search_dlg is None or not self._global_search_dlg.isVisible():
            self._global_search_dlg = GlobalSearchDialog(
                self._settings, _navigate, parent=self
            )
        self._global_search_dlg.show_and_focus()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not confirm_yes_no(
            self,
            "Quitter",
            "Fermer l'application SME ?",
            default_no=True,
        ):
            event.ignore()
            return
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
