"""Fenêtre principale — menu, sidebar brandée, navigation icônes."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend import __version__
from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import DashboardService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.resources import icon_path
from sme_gend.ui.views.home_view import HomeView
from sme_gend.ui.views.layout_editor_view import LayoutEditorView
from sme_gend.ui.views.liste_present_view import ListePresentView
from sme_gend.ui.views.personnel_list_view import PersonnelListView
from sme_gend.ui.views.referentiel_views import (
    FonctionsView,
    GradesView,
    StructureSubdivisionView,
    StructuresView,
    SubdivisionsView,
)


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
    ) -> None:
        super().__init__()
        self.setWindowTitle("SME — Situation mensuelle des effectifs")
        self.resize(1400, 820)
        self.setMinimumSize(1024, 680)

        style = self.style()

        self._stack = QStackedWidget()
        self._stack.setObjectName("contentStack")

        self._nav = QListWidget()
        self._nav.setObjectName("navList")
        self._nav.setSpacing(2)
        self._nav.setFrameShape(QFrame.Shape.NoFrame)

        def add_page(text: str, widget: QWidget, icon: QIcon) -> None:
            self._stack.addWidget(widget)
            item = QListWidgetItem(icon, "  " + text)
            item.setSizeHint(item.sizeHint().expandedTo(QSize(200, 40)))
            self._nav.addItem(item)

        add_page(
            "Accueil",
            HomeView(settings, dashboard),
            _std_icon(style, QStyle.StandardPixmap.SP_DirHomeIcon),
        )
        add_page(
            "Liste générale",
            PersonnelListView(personnel),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogListView),
        )
        add_page(
            "Mise en page liste",
            LayoutEditorView(layout_cfg),
            _std_icon(style, QStyle.StandardPixmap.SP_FileDialogDetailedView),
        )
        add_page(
            "Structures",
            StructuresView(referentiel),
            _std_icon(style, QStyle.StandardPixmap.SP_DesktopIcon),
        )
        add_page(
            "Struct. ↔ subdivisions",
            StructureSubdivisionView(referentiel),
            _std_icon(style, QStyle.StandardPixmap.SP_ArrowRight),
        )
        add_page(
            "Subdivisions",
            SubdivisionsView(referentiel),
            _std_icon(style, QStyle.StandardPixmap.SP_DirIcon),
        )
        add_page(
            "Grades",
            GradesView(referentiel),
            _std_icon(style, QStyle.StandardPixmap.SP_MessageBoxInformation),
        )
        add_page(
            "Fonctions",
            FonctionsView(referentiel),
            _std_icon(
                style,
                getattr(
                    QStyle.StandardPixmap,
                    "SP_UserIcon",
                    QStyle.StandardPixmap.SP_FileDialogContentsView,
                ),
            ),
        )
        add_page(
            "Liste des présents",
            ListePresentView(liste_present),
            _std_icon(style, QStyle.StandardPixmap.SP_DialogApplyButton),
        )

        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)

        # En-tête sidebar : logo + titres + replier
        logo_path = icon_path("app.svg")
        logo_lbl = QLabel()
        if logo_path.is_file():
            logo_lbl.setPixmap(QIcon(str(logo_path)).pixmap(52, 52))
        logo_lbl.setFixedSize(56, 56)

        brand = QLabel("GENDARMERIE NATIONALE")
        brand.setObjectName("sidebarBrand")
        title = QLabel("SME")
        title.setObjectName("sidebarTitle")
        sub = QLabel("Effectifs")
        sub.setStyleSheet("color: #b0bec5; font-size: 12px;")

        head_txt = QVBoxLayout()
        head_txt.setSpacing(2)
        head_txt.addWidget(brand)
        head_txt.addWidget(title)
        head_txt.addWidget(sub)

        self._btn_collapse_sidebar = QToolButton()
        self._btn_collapse_sidebar.setText("◀")
        self._btn_collapse_sidebar.setObjectName("sidebarCollapseBtn")
        self._btn_collapse_sidebar.setToolTip("Replier la barre latérale")
        self._btn_collapse_sidebar.setAutoRaise(True)
        self._btn_collapse_sidebar.clicked.connect(self._collapse_sidebar)

        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        head_row.addWidget(logo_lbl, alignment=Qt.AlignmentFlag.AlignTop)
        head_row.addLayout(head_txt, stretch=1)
        head_row.addWidget(self._btn_collapse_sidebar, alignment=Qt.AlignmentFlag.AlignTop)

        head_w = QWidget()
        head_w.setLayout(head_row)

        self._sidebar_frame = QFrame()
        self._sidebar_frame.setObjectName("sidebarFrame")
        side_lay = QVBoxLayout(self._sidebar_frame)
        side_lay.setContentsMargins(12, 20, 12, 16)
        side_lay.setSpacing(8)
        side_lay.addWidget(head_w)
        side_lay.addWidget(self._nav, stretch=1)

        self._rail = QFrame()
        self._rail.setObjectName("sidebarRail")
        self._rail.setFixedWidth(44)
        self._rail.hide()
        rail_lay = QVBoxLayout(self._rail)
        rail_lay.setContentsMargins(4, 16, 4, 8)
        self._btn_expand_sidebar = QToolButton()
        self._btn_expand_sidebar.setText("▶")
        self._btn_expand_sidebar.setObjectName("sidebarExpandBtn")
        self._btn_expand_sidebar.setToolTip("Afficher la barre latérale")
        self._btn_expand_sidebar.setAutoRaise(True)
        self._btn_expand_sidebar.clicked.connect(self._expand_sidebar)
        rail_lay.addWidget(self._btn_expand_sidebar)
        rail_lay.addStretch()

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._rail)
        lay.addWidget(self._sidebar_frame)
        lay.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

        self._build_menu_bar()

        status = QStatusBar()
        self.setStatusBar(status)
        db = str(settings.database_path)
        status.showMessage("Prêt")
        status.addPermanentWidget(QLabel(f"Base : {db}"))

    def _build_menu_bar(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("&Fichier")
        act_quit = QAction("Quitter", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_view = bar.addMenu("&Affichage")
        act_reload = QAction("Actualiser le tableau de bord", self)
        act_reload.setShortcut("F5")
        act_reload.triggered.connect(self._refresh_dashboard_if_visible)
        m_view.addAction(act_reload)
        act_sidebar = QAction("Afficher / masquer la barre latérale", self)
        act_sidebar.setShortcut("Ctrl+B")
        act_sidebar.triggered.connect(self._toggle_sidebar)
        m_view.addAction(act_sidebar)

        m_nav = bar.addMenu("&Navigation")
        nav_entries = [
            "Accueil",
            "Liste générale",
            "Mise en page liste",
            "Structures",
            "Struct. ↔ subdivisions",
            "Subdivisions",
            "Grades",
            "Fonctions",
            "Liste des présents",
        ]
        for idx, label in enumerate(nav_entries):
            act = QAction(label, self)
            act.triggered.connect(lambda checked=False, i=idx: self._go_page(i))
            m_nav.addAction(act)

        m_help = bar.addMenu("&Aide")
        act_about = QAction("À propos de SME…", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _refresh_dashboard_if_visible(self) -> None:
        if self._nav.currentRow() != 0:
            return
        w = self._stack.widget(0)
        if hasattr(w, "_reload_stats"):
            w._reload_stats()
            self.statusBar().showMessage("Indicateurs actualisés.", 3000)

    def _go_page(self, index: int) -> None:
        if 0 <= index < self._nav.count():
            self._nav.setCurrentRow(index)

    def _collapse_sidebar(self) -> None:
        self._sidebar_frame.setVisible(False)
        self._rail.setVisible(True)

    def _expand_sidebar(self) -> None:
        self._sidebar_frame.setVisible(True)
        self._rail.setVisible(False)

    def _toggle_sidebar(self) -> None:
        if self._sidebar_frame.isVisible():
            self._collapse_sidebar()
        else:
            self._expand_sidebar()

    def _about(self) -> None:
        m = QMessageBox(self)
        m.setWindowTitle("À propos")
        m.setIcon(QMessageBox.Icon.Information)
        m.setTextFormat(Qt.TextFormat.RichText)
        m.setText(
            f"<h3>SME — Situation mensuelle des effectifs</h3>"
            f"<p>Application de gestion et d’export des listes de personnel.</p>"
            f"<p><b>Version</b> {__version__}</p>"
            f"<p><b>Réalisation</b> : "
            f"<a href=\"https://smeth-dev.site\">smeth-dev.site</a></p>"
            f"<p style=\"color:#666;\">Client desktop PySide6 — données Access.</p>"
        )
        m.setOpenExternalLinks(True)
        m.exec()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
