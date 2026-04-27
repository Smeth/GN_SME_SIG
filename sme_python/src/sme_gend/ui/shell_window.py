"""Fenêtre principale — menu, sidebar brandée, navigation arborescente."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
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
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_preview_service import DetachesPreviewService
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
from sme_gend.ui.views.numerique_indispo_view import NumeriqueIndispoView
from sme_gend.ui.views.numerique_matrice_view import NumeriqueMatriceView
from sme_gend.ui.views.numerique_satellite_view import NumeriqueSatelliteView
from sme_gend.ui.views.personnel_list_view import PersonnelListView
from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, StructuresView
from sme_gend.ui.navigation_pages import (
    NAV_MENU_SEPARATOR_BEFORE,
    PAGE_COUNT,
    PAGE_DASHBOARD,
    PAGE_ARCHIVES_CLOSURE,
    PAGE_DETACHES,
    PAGE_FONCTIONS,
    PAGE_GRADES,
    PAGE_LAYOUT_EDITOR,
    PAGE_LISTE_GENERALE,
    PAGE_LISTE_PRESENTS,
    PAGE_NUMERIQUE_INDISPO,
    PAGE_NUMERIQUE_MATRICE,
    PAGE_NUMERIQUE_SATELLITE,
    PAGE_SETTINGS,
    PAGE_STRUCTURES,
    PAGE_DETACHES_CONFIG,
)
from sme_gend.ui.views.archives_view import ArchivesView
from sme_gend.ui.views.settings_view import SettingsView

# Données utilisateur sur QTreeWidgetItem : indice pile (-1 = dossier non navigable)
_ROLE_STACK = Qt.ItemDataRole.UserRole
_ROLE_LABEL = Qt.ItemDataRole.UserRole + 1


def _std_icon(style: QStyle, sp: QStyle.StandardPixmap) -> QIcon:
    return style.standardIcon(sp)


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

        # --- Pages (ordre pile = index utilisé partout) ---
        register_page(
            "Tableau de bord",
            HomeView(settings, dashboard, navigate_to_page=self._go_page),
        )
        register_page("Liste générale", PersonnelListView(personnel, referentiel))
        register_page(
            "Mise en page liste",
            LayoutEditorView(layout_cfg, detaches_cfg),
        )
        register_page("Liste des présents", ListePresentView(liste_present))
        register_page("Détachés", DetachesView(settings, detaches_preview, detaches_cfg))
        register_page("Structures", StructuresView(referentiel))
        register_page("Grades", GradesView(referentiel))
        register_page("Fonctions", FonctionsView(referentiel))
        register_page("Config. fichier DETACHES", DetachesConfigView(detaches_cfg))
        register_page(
            "Matrice (situation numérique)",
            NumeriqueMatriceView(settings, numerique_preview),
        )
        register_page(
            "Indispo par structure",
            NumeriqueIndispoView(settings, numerique_preview),
        )
        register_page(
            "Listes détail (situation num.)",
            NumeriqueSatelliteView(settings, numerique_preview),
        )
        register_page("Archives clôture", ArchivesView())
        register_page("Paramètres", SettingsView(settings))

        assert self._stack.count() == PAGE_COUNT, (
            f"navigation_pages.PAGE_COUNT={PAGE_COUNT} != pile={self._stack.count()} "
            "(mettre à jour navigation_pages si l’ordre des pages change)."
        )

        # Icônes réelles (register_page a mis des QIcon() vides pour index 0)
        icons: list[QIcon] = [
            _std_icon(style, QStyle.StandardPixmap.SP_DirHomeIcon),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogListView),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogDetailedView),
            _std_icon(style, QStyle.StandardPixmap.SP_DialogApplyButton),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogContentsView),
            _std_icon(style, QStyle.StandardPixmap.SP_DesktopIcon),
            _std_icon(style, QStyle.StandardPixmap.SP_MessageBoxInformation),
            _std_icon(
                style,
                getattr(
                    QStyle.StandardPixmap,
                    "SP_UserIcon",
                    QStyle.StandardPixmap.SP_FileDialogContentsView,
                ),
            ),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogInfoView),
            _std_icon(style, QStyle.StandardPixmap.SP_ComputerIcon),
            _std_icon(
                style,
                getattr(
                    QStyle.StandardPixmap,
                    "SP_FileDialogStart",
                    QStyle.StandardPixmap.SP_CommandLink,
                ),
            ),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogListView),
            _std_icon(style, QStyle.StandardPixmap.SP_DirLinkIcon),
            _std_icon(
                style,
                getattr(
                    QStyle.StandardPixmap,
                    "SP_FileDialogDetailedView",
                    QStyle.StandardPixmap.SP_FileDialogInfoView,
                ),
            ),
        ]

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
            "Effectifs & listes",
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogNewFolder),
        )
        add_leaf(f_eff, nl[PAGE_LISTE_GENERALE], PAGE_LISTE_GENERALE)
        add_leaf(f_eff, nl[PAGE_LAYOUT_EDITOR], PAGE_LAYOUT_EDITOR)
        add_leaf(f_eff, nl[PAGE_LISTE_PRESENTS], PAGE_LISTE_PRESENTS)

        f_det = add_folder(
            "Détachés",
            _std_icon(
                style,
                getattr(QStyle.StandardPixmap, "SP_DriveNetIcon", QStyle.StandardPixmap.SP_DirIcon),
            ),
        )
        add_leaf(f_det, "Liste & export", PAGE_DETACHES)

        f_num = add_folder(
            "Situation numérique",
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogInfoView),
        )
        add_leaf(f_num, nl[PAGE_NUMERIQUE_MATRICE], PAGE_NUMERIQUE_MATRICE)
        add_leaf(f_num, nl[PAGE_NUMERIQUE_INDISPO], PAGE_NUMERIQUE_INDISPO)
        add_leaf(f_num, nl[PAGE_NUMERIQUE_SATELLITE], PAGE_NUMERIQUE_SATELLITE)

        f_ref = add_folder(
            "Référentiels",
            _std_icon(style, QStyle.StandardPixmap.SP_DialogOpenButton),
        )
        add_leaf(f_ref, nl[PAGE_STRUCTURES], PAGE_STRUCTURES)
        add_leaf(f_ref, nl[PAGE_GRADES], PAGE_GRADES)
        add_leaf(f_ref, nl[PAGE_FONCTIONS], PAGE_FONCTIONS)
        add_leaf(f_ref, nl[PAGE_DETACHES_CONFIG], PAGE_DETACHES_CONFIG)

        add_leaf(None, nl[PAGE_ARCHIVES_CLOSURE], PAGE_ARCHIVES_CLOSURE)
        add_leaf(None, nl[PAGE_SETTINGS], PAGE_SETTINGS)

        self._nav.itemClicked.connect(self._on_nav_item_clicked)
        self._nav.setCurrentItem(self._page_to_item[PAGE_DASHBOARD])
        self._stack.setCurrentIndex(PAGE_DASHBOARD)

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
        self._btn_help_sidebar.setToolTip("À propos / aide — F1 ou Ctrl+H")
        self._btn_help_sidebar.setAutoRaise(True)
        self._btn_help_sidebar.setMinimumSize(36, 36)
        self._btn_help_sidebar.clicked.connect(self._about)
        help_row = QHBoxLayout()
        help_row.addStretch()
        help_row.addWidget(self._btn_help_sidebar)
        help_row.addStretch()
        self._side_lay.addLayout(help_row)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._sidebar_frame)
        lay.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        self._settings = settings
        self._build_menu_bar()

        status = QStatusBar()
        self.setStatusBar(status)
        status.showMessage("Prêt")
        self._status_data_lbl = QLabel()
        self._refresh_data_status_label()
        status.addPermanentWidget(self._status_data_lbl)

        for i in range(self._stack.count()):
            w = self._stack.widget(i)
            if isinstance(w, ListePresentView):
                w.set_open_layout_editor(lambda: self._go_page(PAGE_LAYOUT_EDITOR))
                break

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
        # Barre de menus intégrée à la fenêtre (évite menus natifs Windows peu fiables avec thème QSS)
        bar.setNativeMenuBar(False)

        m_file = bar.addMenu("&Fichier")
        act_paths = QAction("Chemins des fichiers de données…", self)
        act_paths.setToolTip(
            "SQLite (données principales), Access (import depuis l’ancien .mdb), modèles Excel, "
            "archives, médias. Enregistrement dans le profil : rechargement immédiat de l’application."
        )
        act_paths.triggered.connect(self._open_paths_config)
        m_file.addAction(act_paths)
        m_file.addSeparator()
        act_quit = QAction("Quitter", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_view = bar.addMenu("&Affichage")
        act_reload = QAction("Actualiser le tableau de bord", self)
        act_reload.setShortcut("F5")
        act_reload.triggered.connect(self._refresh_dashboard_if_visible)
        m_view.addAction(act_reload)
        act_sidebar = QAction("Réduire / développer la barre latérale (icônes)", self)
        act_sidebar.setShortcut("Ctrl+B")
        act_sidebar.triggered.connect(self._toggle_sidebar)
        m_view.addAction(act_sidebar)

        m_nav = bar.addMenu("&Navigation")
        for idx, label in enumerate(self._nav_labels):
            if idx in NAV_MENU_SEPARATOR_BEFORE:
                m_nav.addSeparator()
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, i=idx: self._go_page(i))
            m_nav.addAction(act)

        m_help = bar.addMenu("&Aide")
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
        self._refresh_dashboard_if_visible()
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
        from sme_gend.ui.views.home_view import HomeView
        from sme_gend.ui.views.liste_present_view import ListePresentView
        from sme_gend.ui.views.numerique_indispo_view import NumeriqueIndispoView
        from sme_gend.ui.views.numerique_matrice_view import NumeriqueMatriceView
        from sme_gend.ui.views.numerique_satellite_view import NumeriqueSatelliteView
        from sme_gend.ui.views.personnel_list_view import PersonnelListView
        from sme_gend.ui.views.referentiel_views import FonctionsView, GradesView, StructuresView
        from sme_gend.ui.views.settings_view import SettingsView

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
            elif isinstance(w, (NumeriqueMatriceView, NumeriqueIndispoView, NumeriqueSatelliteView)):
                w.apply_runtime_settings(s)
            elif isinstance(w, (StructuresView, GradesView, FonctionsView)):
                w.apply_runtime_after_config_change()
            elif isinstance(w, SettingsView):
                w.apply_runtime_settings(s)

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
            # Icônes alignées : pas d’indentation de branche (évite décalage + chevauchement scrollbar)
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
        # n’affichent pas correctement la boîte « À propos » (fenêtre invisible ou bloquée).
        dlg = QDialog(self)
        dlg.setWindowTitle("À propos — SME")
        dlg.setModal(True)
        dlg.setMinimumWidth(420)
        body = QLabel(
            f"<h3 style=\"margin-top:0;\">SME — Situation mensuelle des effectifs</h3>"
            f"<p>Application de gestion et d’export des listes de personnel.</p>"
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
