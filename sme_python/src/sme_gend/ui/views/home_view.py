"""Vue Accueil — tableau de bord (synthèse effectifs, TabSme, graphiques)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QHorizontalBarSeries,
    QLineSeries,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings, liste_layout_config_path
from sme_gend.ui.navigation_pages import PAGE_ARCHIVES_CLOSURE, PAGE_STRUCTURES
from sme_gend.config.sme_context import load_sme_context, save_sme_context
from sme_gend.services.sme_month_closure import (
    extract_yyyy_mm,
    run_piste_a_month_closure,
    sme_archives_dir,
)
from sme_gend.services.dashboard_service import (
    DashboardKpiCard,
    DashboardService,
    DashboardSnapshot,
)
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no

_SME_MONTHS_FR = (
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
)


def _sme_month_combo(*, for_dialog: bool = False) -> QComboBox:
    cb = QComboBox()
    cb.setObjectName("smePeriodComboDialog" if for_dialog else "smePeriodCombo")
    for i, name in enumerate(_SME_MONTHS_FR, start=1):
        cb.addItem(name, i)
    cb.setMinimumWidth(170)
    cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    return cb


def _sme_year_spin(*, for_dialog: bool = False) -> QSpinBox:
    sp = QSpinBox()
    sp.setObjectName("smePeriodYearSpinDialog" if for_dialog else "smePeriodYearSpin")
    sp.setRange(2000, 2100)
    sp.setValue(date.today().year)
    sp.setMinimumWidth(96)
    return sp


def _widgets_to_ym(cb: QComboBox, sp: QSpinBox) -> str:
    m = cb.currentData()
    y = sp.value()
    if m is None:
        return ""
    return f"{y:04d}-{int(m):02d}"


def _ym_to_widgets(ym_hint: str, cb: QComboBox, sp: QSpinBox) -> None:
    ym = extract_yyyy_mm(ym_hint)
    if ym:
        y_s, mo_s = ym.split("-")
        sp.setValue(int(y_s))
        cb.setCurrentIndex(int(mo_s) - 1)
    else:
        d = date.today()
        sp.setValue(d.year)
        cb.setCurrentIndex(d.month - 1)


def _next_yyyy_mm(ym: str) -> str:
    s = extract_yyyy_mm(ym)
    if not s:
        return ""
    y, m = map(int, s.split("-"))
    if m == 12:
        return f"{y + 1:04d}-01"
    return f"{y:04d}-{m + 1:02d}"


_PALETTE = [
    "#1a237e",
    "#3949ab",
    "#5c6bc0",
    "#7986cb",
    "#9fa8da",
    "#ffd54f",
    "#ffb300",
    "#00897b",
    "#c62828",
    "#6d4c41",
    "#7b1fa2",
    "#00695c",
    "#ef6c00",
]


_KPI_ICON_META: dict[str, tuple[QStyle.StandardPixmap, str]] = {
    "effectifs": (QStyle.StandardPixmap.SP_DriveHDIcon, "#eceff1"),
    "present": (QStyle.StandardPixmap.SP_DialogApplyButton, "#e8f5e9"),
    "detache": (QStyle.StandardPixmap.SP_ArrowLeft, "#e3f2fd"),
    "stage_interne": (QStyle.StandardPixmap.SP_FileDialogInfoView, "#f3e5f5"),
    "mission": (QStyle.StandardPixmap.SP_ArrowForward, "#e3f2fd"),
    "malade": (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"),
    "deserteur": (QStyle.StandardPixmap.SP_MessageBoxWarning, "#ffcdd2"),
    "stage_local": (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"),
    "stage_ex": (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"),
    "inst_mr": (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"),
    "conge_admin": (QStyle.StandardPixmap.SP_FileDialogListView, "#e3f2fd"),
    "punis": (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"),
    "taux": (QStyle.StandardPixmap.SP_FileDialogListView, "#fff9c4"),
}


def _kpi_value_color(variant: str) -> str | None:
    return {
        "success": "#2e7d32",
        "alert": "#c62828",
        "highlight": "#f57f17",
        "info": "#1565c0",
        "brand": "#6a1b9a",
    }.get(variant)


def _kpi_icon_badge(parent: QWidget, kpi_id: str) -> QFrame:
    pix_kind, bg = _KPI_ICON_META.get(
        kpi_id, (QStyle.StandardPixmap.SP_FileIcon, "#eceff1")
    )
    pm = parent.style().standardIcon(pix_kind).pixmap(22, 22)
    box = QFrame()
    box.setFixedSize(44, 44)
    box.setStyleSheet(
        f"background-color: {bg}; border-radius: 10px; border: none;"
    )
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    ic = QLabel()
    ic.setPixmap(pm)
    ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(ic)
    return box


def _dash_kpi_card(parent: QWidget, card: DashboardKpiCard) -> QFrame:
    """Carte indicateur (liste générale / Padmin) — données : DashboardService."""
    f = QFrame()
    f.setObjectName("dashKpiCard")
    if card.variant == "alert":
        frame_ss = (
            "QFrame#dashKpiCard { background: #ffffff; border-radius: 10px; "
            "border: 1px solid #ffcdd2; border-left: 4px solid #c62828; }"
        )
    elif card.variant == "highlight":
        frame_ss = (
            "QFrame#dashKpiCard { background: #fffde7; border-radius: 10px; "
            "border: 1px solid #ffe082; }"
        )
    else:
        frame_ss = (
            "QFrame#dashKpiCard { background: #ffffff; border-radius: 10px; "
            "border: 1px solid #e0e0e0; }"
        )
    f.setStyleSheet(frame_ss)

    outer = QVBoxLayout(f)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(4)

    top = QHBoxLayout()
    top.setSpacing(8)
    title = QLabel(card.title)
    title.setObjectName("dashKpiCardTitle")
    title.setWordWrap(True)
    title.setStyleSheet("color: #424242; font-size: 12px;")
    top.addWidget(title, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
    top.addWidget(_kpi_icon_badge(parent, card.kpi_id), alignment=Qt.AlignmentFlag.AlignTop)
    outer.addLayout(top)

    val = QLabel(card.value)
    val.setObjectName("dashKpiCardValue")
    vc = _kpi_value_color(card.variant)
    if vc:
        val.setStyleSheet(
            f"color: {vc}; font-weight: 600; font-size: 22px; font-family: 'Segoe UI', sans-serif;"
        )
    else:
        val.setStyleSheet(
            "color: #212121; font-weight: 600; font-size: 22px; font-family: 'Segoe UI', sans-serif;"
        )
    outer.addWidget(val)

    if card.subtitle:
        sub = QLabel(card.subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #757575; font-size: 11px;")
        outer.addWidget(sub)

    outer.addStretch()
    return f


def _pos_card(label: str, count: str) -> QFrame:
    f = QFrame()
    f.setObjectName("dashPosCard")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(14, 12, 14, 12)
    t = QLabel(label)
    t.setObjectName("dashPosCardTitle")
    t.setWordWrap(True)
    v = QLabel(count)
    v.setObjectName("dashPosCardValue")
    lay.addWidget(t)
    lay.addWidget(v)
    return f


def _truncate(s: str, max_len: int = 42) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _chart_theme(chart: QChart) -> None:
    chart.setBackgroundVisible(True)
    chart.setBackgroundBrush(QColor("#ffffff"))
    chart.legend().setVisible(True)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignBottom)
    chart.setTitleFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))


def _make_chart_view(chart: QChart) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setMinimumHeight(260)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
    return view


def _pie_presence(presents: int, indisponibles: int) -> QChartView:
    series = QPieSeries()
    series.setHoleSize(0.45)
    series.setPieSize(0.85)
    if presents > 0:
        sl = series.append("Présents", float(presents))
        sl.setBrush(QColor("#1a237e"))
        sl.setLabelColor(QColor("#ffffff"))
    if indisponibles > 0:
        sl = series.append("Indisponibles", float(indisponibles))
        sl.setBrush(QColor("#c62828"))
        sl.setLabelColor(QColor("#ffffff"))
    if presents == 0 and indisponibles == 0:
        series.append("Aucune donnée", 1.0)
    for s in series.slices():
        s.setLabelVisible(True)
        s.setLabel(f"{s.label()} : {int(s.value())}")
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle("Répartition présence (liste générale)")
    _chart_theme(chart)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
    return _make_chart_view(chart)


def _hbar_grouped_multi(items: list[tuple[str, int]], title: str) -> QChartView:
    """Barres horizontales : une couleur par barre (jeu de séries creux)."""
    top = items[:14]
    if not top:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    rev = list(reversed(top))
    n = len(rev)
    series = QHorizontalBarSeries()
    for i, (name, val) in enumerate(rev):
        bs = QBarSet("")
        row = [0.0] * n
        row[i] = float(val)
        for v in row:
            bs << v
        bs.setBrush(QColor(_PALETTE[i % len(_PALETTE)]))
        series.append(bs)
    labels = [_truncate(rev[j][0], 36) for j in range(n)]
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    _chart_theme(chart)
    ay = QBarCategoryAxis()
    ay.append(labels)
    chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(ay)
    ax = QValueAxis()
    ax.setLabelFormat("%d")
    ax.setTitleText("Personnes")
    chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
    series.attachAxis(ax)
    return _make_chart_view(chart)


def _bar_vertical_multi(items: list[tuple[str, int]], title: str) -> QChartView:
    """Histogramme vertical : une couleur par colonne."""
    if not items:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    n = len(items)
    series = QBarSeries()
    for i, (name, val) in enumerate(items):
        bs = QBarSet("")
        row = [0.0] * n
        row[i] = float(val)
        for v in row:
            bs << v
        bs.setBrush(QColor(_PALETTE[i % len(_PALETTE)]))
        series.append(bs)
    labels = [_truncate(items[j][0], 12) for j in range(n)]
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    _chart_theme(chart)
    ax = QBarCategoryAxis()
    ax.append(labels)
    chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
    series.attachAxis(ax)
    ax.setLabelsAngle(-45)
    ay = QValueAxis()
    ay.setLabelFormat("%d")
    ay.setTitleText("Personnes")
    chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(ay)
    return _make_chart_view(chart)


def _hbar_named_multi(items: list[tuple[str, int]], title: str) -> QChartView:
    if not items:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    rev = list(reversed(items))
    n = len(rev)
    series = QHorizontalBarSeries()
    for i, (name, val) in enumerate(rev):
        bs = QBarSet("")
        row = [0.0] * n
        row[i] = float(val)
        for v in row:
            bs << v
        bs.setBrush(QColor(_PALETTE[i % len(_PALETTE)]))
        series.append(bs)
    labels = [_truncate(rev[j][0], 32) for j in range(n)]
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    _chart_theme(chart)
    ay = QBarCategoryAxis()
    ay.append(labels)
    chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
    series.attachAxis(ay)
    ax = QValueAxis()
    ax.setLabelFormat("%d")
    chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
    series.attachAxis(ax)
    return _make_chart_view(chart)


def _sme_trend_line(points: list[tuple[str, float]], title: str) -> QChartView:
    """Tendance Statut (score 0–1) selon l’ordre chronologique des mois TabSme."""
    if not points:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    line = QLineSeries()
    line.setName("Score (Statut)")
    line.setColor(QColor("#1a237e"))
    for i, (_lab, y) in enumerate(points):
        line.append(float(i), float(y))
    chart = QChart()
    chart.addSeries(line)
    chart.setTitle(title)
    _chart_theme(chart)
    ax = QValueAxis()
    ax.setLabelFormat("%d")
    ax.setRange(0, max(len(points) - 1, 0))
    ax.setTitleText("Indice chronologique (voir tableau des mois ci-dessus)")
    chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
    line.attachAxis(ax)
    ay = QValueAxis()
    ay.setRange(0, 1)
    ay.setLabelFormat("%.2f")
    ay.setTitleText("Score dérivé du Statut")
    chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
    line.attachAxis(ay)
    chart.legend().setVisible(False)
    return _make_chart_view(chart)


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
        self._navigate_to_page = navigate_to_page
        self._page_scroll: QScrollArea | None = None

        title = QLabel("Tableau de bord")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Indicateurs liste générale, périodes SME (TabSme), positions, structures et grades."
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

        self._sme_frame = QFrame()
        self._sme_frame.setObjectName("dashSmeFrame")
        sme_outer = QVBoxLayout(self._sme_frame)
        sme_outer.setSpacing(12)
        self._sme_hint = QLabel()
        self._sme_hint.setWordWrap(True)
        self._sme_hint.setObjectName("dashSubtitle")
        self._sme_table = QTableWidget()
        self._sme_table.setColumnCount(3)
        self._sme_table.setHorizontalHeaderLabels(["Code SME", "Mois / période", "Statut"])
        self._sme_table.horizontalHeader().setStretchLastSection(True)
        self._sme_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._sme_table.setAlternatingRowColors(True)
        self._sme_trend_holder = QWidget()
        self._sme_trend_lay = QVBoxLayout(self._sme_trend_holder)
        self._sme_trend_lay.setContentsMargins(0, 0, 0, 0)

        self._sme_workflow = QGroupBox("Mois en cours — contexte local")
        self._sme_workflow.setObjectName("dashSmeWorkflow")
        sw_lay = QVBoxLayout(self._sme_workflow)
        sw_lay.setSpacing(10)
        sw_hint = QLabel(
            "Choisissez la <b>période SME</b> utilisée pour l’audit des fiches "
            "(<code>sme_mois_context</code>). La <b>clôture</b> met à jour TabSme et archivera SQLite / "
            "mise en page si configuré. Réglages des bases : page <b>Paramètres</b>."
        )
        sw_hint.setWordWrap(True)
        sw_hint.setObjectName("dashSubtitle")
        sw_lay.addWidget(sw_hint)

        self._sme_month = _sme_month_combo()
        self._sme_year = _sme_year_spin()
        self._sme_preview = QLabel()
        self._sme_preview.setObjectName("dashSubtitle")
        self._sme_preview.setStyleSheet("color: #1a237e; font-weight: 600; font-size: 13px;")

        self._sme_month.currentIndexChanged.connect(lambda: self._sync_sme_preview_label())
        self._sme_year.valueChanged.connect(self._sync_sme_preview_label)

        self._sme_jour = QSpinBox()
        self._sme_jour.setObjectName("smePeriodDaySpin")
        self._sme_jour.setRange(0, 31)
        self._sme_jour.setSpecialValueText("—")
        self._sme_jour.setValue(0)
        self._sme_jour.setToolTip(
            "Jour de clôture de référence (optionnel, fichier local uniquement)."
        )

        grid_my = QGridLayout()
        grid_my.setHorizontalSpacing(14)
        grid_my.setVerticalSpacing(8)
        lab_m = QLabel("Mois")
        lab_y = QLabel("Année")
        lab_j = QLabel("Jour")
        for lb in (lab_m, lab_y, lab_j):
            lb.setStyleSheet("color: #424242; font-weight: 500;")
        grid_my.addWidget(lab_m, 0, 0)
        grid_my.addWidget(self._sme_month, 0, 1)
        grid_my.addWidget(lab_y, 0, 2)
        grid_my.addWidget(self._sme_year, 0, 3)
        grid_my.addWidget(lab_j, 0, 4)
        grid_my.addWidget(self._sme_jour, 0, 5)
        grid_my.setColumnStretch(6, 1)
        sw_lay.addLayout(grid_my)
        sw_lay.addWidget(self._sme_preview)

        sw_actions = QHBoxLayout()
        sw_actions.setSpacing(10)
        self._btn_sme_save = QPushButton("Enregistrer le mois actif")
        self._btn_sme_save.clicked.connect(self._on_sme_save_mois)
        self._btn_sme_close = QPushButton("Clôturer le mois…")
        self._btn_sme_close.setToolTip(
            "Archives locales, mise à jour TabSme (Fermé / Ouvert), nouveau mois actif."
        )
        self._btn_sme_close.clicked.connect(self._on_sme_cloture_dialog)
        self._btn_sme_archives = QPushButton("Dossier archives…")
        self._btn_sme_archives.setToolTip(
            "Ouvre le dossier des copies (SQLite, JSON mise en page) dans l’explorateur. "
            "Pour la liste détaillée : onglet « Archives clôture » dans la barre latérale."
        )
        self._btn_sme_archives.clicked.connect(self._on_open_sme_archives_dir)

        self._btn_sme_archives_page = QPushButton("Onglet Archives…")
        self._btn_sme_archives_page.setToolTip("Affiche la page Archives clôture (liste, ouverture des fichiers).")
        self._btn_sme_archives_page.clicked.connect(self._on_go_archives_page)

        sw_actions.addWidget(self._btn_sme_save)
        sw_actions.addWidget(self._btn_sme_close)
        sw_actions.addWidget(self._btn_sme_archives)
        sw_actions.addWidget(self._btn_sme_archives_page)
        sw_actions.addStretch()
        sw_lay.addLayout(sw_actions)

        self._sme_fermes_label = QLabel()
        self._sme_fermes_label.setWordWrap(True)
        self._sme_fermes_label.setObjectName("dashSubtitle")
        sw_lay.addWidget(self._sme_fermes_label)

        sme_outer.addWidget(self._sme_workflow)

        sep_sme = QFrame()
        sep_sme.setFrameShape(QFrame.Shape.HLine)
        sep_sme.setFrameShadow(QFrame.Shadow.Sunken)
        sep_sme.setStyleSheet("background: #e1e4e8; max-height: 2px; border: none;")
        sme_outer.addWidget(sep_sme)

        self._sme_tabsme_heading = QLabel(
            "<b>Historique TabSme</b> — lu depuis la base (Access ou SQLite selon vos paramètres)",
            textFormat=Qt.TextFormat.RichText,
        )
        self._sme_tabsme_heading.setObjectName("dashSubtitle")
        sme_outer.addWidget(self._sme_tabsme_heading)
        sme_outer.addWidget(self._sme_hint)
        sme_outer.addWidget(self._sme_table)
        sme_outer.addWidget(self._sme_trend_holder)

        self._refresh_sme_workflow_widgets()

        self._grid = QGridLayout()
        self._grid.setSpacing(16)

        self._pos_scroll = QScrollArea()
        self._pos_scroll.setObjectName("dashPosScroll")
        self._pos_scroll.setWidgetResizable(True)
        self._pos_scroll.setMinimumHeight(160)
        self._pos_scroll.setMaximumHeight(320)
        self._pos_inner = QWidget()
        self._pos_grid = QGridLayout(self._pos_inner)
        self._pos_grid.setSpacing(12)
        self._pos_scroll.setWidget(self._pos_inner)

        self._charts_wrap = QWidget()
        self._charts_layout = QGridLayout(self._charts_wrap)
        self._charts_layout.setSpacing(16)

        self._grade_table = QTableWidget()
        self._grade_table.setObjectName("dashGradeTable")
        self._grade_table.setColumnCount(4)
        self._grade_table.setHorizontalHeaderLabels(
            ["Grade (liste générale)", "Effectif", "% du total", "Écart vs répartition uniforme"]
        )
        self._grade_table.horizontalHeader().setStretchLastSection(True)
        self._grade_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._grade_table.setAlternatingRowColors(True)
        self._grade_table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._grade_table.verticalHeader().setVisible(False)
        self._grade_hint = QLabel(
            "Pour chaque libellé de grade : effectif, part du personnel total, et "
            "<b>écart en points de pourcentage</b> par rapport à une répartition <i>uniforme</i> "
            "(si tous les grades observés avaient le même effectif)."
        )
        self._grade_hint.setWordWrap(True)
        self._grade_hint.setObjectName("dashSubtitle")

        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Position administrative", "Effectif"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.verticalHeader().setVisible(False)

        paths = QFrame()
        paths.setObjectName("dashPaths")
        pl = QVBoxLayout(paths)
        pl.setSpacing(8)
        pl.addWidget(QLabel("<b>Raccourcis fichiers</b>", textFormat=Qt.TextFormat.RichText))
        self._path_labels: list[QLabel] = []
        for _ in range(7):
            lab = QLabel()
            lab.setObjectName("dashPathLabel")
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._path_labels.append(lab)
            pl.addWidget(lab)

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(20)
        inner_lay.addLayout(head)
        inner_lay.addWidget(sub)

        inner_lay.addWidget(
            QLabel(
                "<b>Indicateurs principaux</b> — effectifs et positions administratives "
                "(ListeGenerale.Padmin), hors filtre de période.",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addLayout(self._grid)

        self._repartitions_block = QWidget()
        rep_outer = QVBoxLayout(self._repartitions_block)
        rep_outer.setSpacing(14)
        rep_outer.setContentsMargins(0, 8, 0, 0)
        self._rep_title = QLabel("RÉPARTITIONS")
        self._rep_title.setStyleSheet(
            "color: #212121; font-size: 12px; font-weight: 700; letter-spacing: 4px;"
        )
        self._rep_cards_row = QWidget()
        self._rep_cards_lay = QHBoxLayout(self._rep_cards_row)
        self._rep_cards_lay.setSpacing(16)
        self._rep_cards_lay.setContentsMargins(0, 0, 0, 0)
        rep_outer.addWidget(self._rep_title)
        rep_outer.addWidget(self._rep_cards_row)
        inner_lay.addWidget(self._repartitions_block)

        inner_lay.addWidget(
            QLabel(
                "<b>Effectifs par position administrative</b> — une carte par libellé",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._pos_scroll)
        inner_lay.addWidget(
            QLabel(
                "<b>Graphiques et courbes</b> — présence, positions, structures, grades",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._charts_wrap)
        inner_lay.addWidget(
            QLabel(
                "<b>Effectifs et répartition par grade</b> — variations par rapport à une base uniforme",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._grade_hint)
        inner_lay.addWidget(self._grade_table)
        inner_lay.addWidget(
            QLabel(
                "<b>Situation mensuelle (SME)</b> — mois de travail, TabSme et tendance",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._sme_frame)
        inner_lay.addWidget(QLabel("<b>Tableau détaillé (positions)</b>", textFormat=Qt.TextFormat.RichText))
        inner_lay.addWidget(self._table)
        inner_lay.addWidget(paths)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        self._page_scroll = scroll

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

        self._reload_stats(silent=True)

    def _sync_sme_preview_label(self) -> None:
        ym = _widgets_to_ym(self._sme_month, self._sme_year)
        if ym:
            mo_i = int(self._sme_month.currentData() or 1)
            nom = _SME_MONTHS_FR[mo_i - 1].lower()
            self._sme_preview.setText(
                f"Période SME : {ym} ({nom} {self._sme_year.value()})"
            )
        else:
            self._sme_preview.setText("Période SME : —")

    def _refresh_sme_history_label_only(self) -> None:
        """Met à jour l’historique des clôtures sans réinitialiser mois / année (ex. après F5)."""
        ctx = load_sme_context()
        if ctx.mois_fermes:
            tail = ctx.mois_fermes[-12:]
            fermes = ", ".join(tail)
            suffix = " …" if len(ctx.mois_fermes) > 12 else ""
            self._sme_fermes_label.setText(
                f"<b>Mois marqués comme clôturés (historique local)</b> : {fermes}{suffix}"
            )
        else:
            self._sme_fermes_label.setText(
                "<b>Mois clôturés</b> : aucun enregistré dans ce fichier."
            )

    def _refresh_sme_workflow_widgets(self) -> None:
        ctx = load_sme_context()
        _ym_to_widgets(ctx.mois_actif, self._sme_month, self._sme_year)
        if ctx.jour_cloture is not None:
            self._sme_jour.setValue(ctx.jour_cloture)
        else:
            self._sme_jour.setValue(0)
        self._sync_sme_preview_label()
        self._refresh_sme_history_label_only()

    def _on_open_sme_archives_dir(self) -> None:
        d = sme_archives_dir().resolve()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(d))):
            QMessageBox.warning(
                self,
                "Archives",
                f"Impossible d’ouvrir l’explorateur. Chemin : \n{d}",
            )

    def _on_go_archives_page(self) -> None:
        if self._navigate_to_page is not None:
            self._navigate_to_page(PAGE_ARCHIVES_CLOSURE)
        else:
            self._on_open_sme_archives_dir()

    def _on_sme_save_mois(self) -> None:
        ym = _widgets_to_ym(self._sme_month, self._sme_year)
        if not ym or extract_yyyy_mm(ym) != ym:
            QMessageBox.warning(
                self,
                "Mois SME",
                "Choisissez un mois et une année valides.",
            )
            return
        ctx = load_sme_context()
        ctx.mois_actif = ym
        jv = self._sme_jour.value()
        ctx.jour_cloture = None if jv == 0 else jv
        save_sme_context(ctx)
        self._refresh_sme_workflow_widgets()
        jour_txt = f" Jour de clôture : {jv}." if jv else ""
        QMessageBox.information(
            self,
            "Mois SME",
            f"Le mois actif « {ym} » a été enregistré.{jour_txt} Les prochaines modifications de fiches "
            "personnel l’indiqueront dans le fichier d’audit (champ sme_mois_context).",
        )

    def _on_sme_cloture_dialog(self) -> None:
        ctx0 = load_sme_context()
        dlg = QDialog(self)
        dlg.setObjectName("SmeClotureDialog")
        dlg.setWindowTitle("Clôturer un mois SME (Piste A)")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        lay.addWidget(
            QLabel(
                "Le mois clôturé est ajouté à l’historique local, le mois suivant devient le mois actif. "
                "Une copie du <b>fichier SQLite</b> (si présent) et du <b>JSON de mise en page liste</b> "
                "est enregistrée sous <code>sme_archives</code>. Les lignes <b>TabSme</b> dont la colonne "
                "<i>mois</i> correspond au mois clôturé passent en statut <b>Fermé</b> ; celles du mois "
                "suivant en <b>Ouvert</b> (ou insertion <code>SME-YYYYMM</code> si besoin). "
                "Confirmez que la base (Access ou SQLite) est accessible en écriture pour TabSme."
            )
        )

        g1 = QGroupBox("Mois à clôturer")
        g1.setObjectName("dashSmeWorkflow")
        f1 = QFormLayout(g1)
        cb_c = _sme_month_combo(for_dialog=True)
        sp_c = _sme_year_spin(for_dialog=True)
        _ym_to_widgets(ctx0.mois_actif, cb_c, sp_c)
        row_c = QHBoxLayout()
        row_c.setSpacing(10)
        row_c.addWidget(QLabel("Mois :"))
        row_c.addWidget(cb_c)
        row_c.addWidget(QLabel("Année :"))
        row_c.addWidget(sp_c)
        row_c.addStretch()
        f1.addRow(row_c)

        g2 = QGroupBox("Nouveau mois actif (après clôture)")
        g2.setObjectName("dashSmeWorkflow")
        f2 = QFormLayout(g2)
        cb_s = _sme_month_combo(for_dialog=True)
        sp_s = _sme_year_spin(for_dialog=True)
        nxt = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
        if nxt:
            _ym_to_widgets(nxt, cb_s, sp_s)
        else:
            _ym_to_widgets("", cb_s, sp_s)

        def _sync_suivant_from_cloture() -> None:
            nn = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
            if nn:
                _ym_to_widgets(nn, cb_s, sp_s)

        btn_fill = QPushButton("Remplir avec le mois suivant (automatique)")
        btn_fill.clicked.connect(_sync_suivant_from_cloture)
        row_s = QHBoxLayout()
        row_s.setSpacing(10)
        row_s.addWidget(QLabel("Mois :"))
        row_s.addWidget(cb_s)
        row_s.addWidget(QLabel("Année :"))
        row_s.addWidget(sp_s)
        row_s.addStretch()
        f2.addRow(row_s)
        f2.addRow(btn_fill)

        lay.addWidget(g1)
        lay.addWidget(g2)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        cloture = _widgets_to_ym(cb_c, sp_c)
        suivant = _widgets_to_ym(cb_s, sp_s)
        if (
            not cloture
            or not suivant
            or extract_yyyy_mm(cloture) != cloture
            or extract_yyyy_mm(suivant) != suivant
        ):
            QMessageBox.warning(
                self,
                "Clôture",
                "Renseignez le mois à clôturer et le nouveau mois actif (mois et année complets).",
            )
            return
        if not confirm_yes_no(
            self,
            "Confirmer la clôture",
            f"Clôturer « {cloture} » et activer « {suivant} » ?\n\n"
            "Des copies d’archive seront créées ; TabSme sera mise à jour.",
            default_no=True,
        ):
            return
        try:
            result = run_piste_a_month_closure(self._settings, cloture, suivant)
        except OSError as e:
            QMessageBox.critical(self, "Clôture", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Clôture", str(e))
            return
        self._refresh_sme_workflow_widgets()
        self._reload_stats(silent=True)
        parts = [
            f"Mois clôturé : « {cloture} ». Mois actif : « {suivant} ».",
            f"TabSme : {result.tabsme_rows_closed} ligne(s) → Fermé, "
            f"{result.tabsme_rows_opened} → Ouvert"
            + (" (dont 1 insertion auto)" if result.tabsme_inserted_suivant else "")
            + ".",
        ]
        if result.sqlite_archive:
            parts.append(f"Archive SQLite : {result.sqlite_archive}")
        if result.layout_archive:
            parts.append(f"Archive mise en page : {result.layout_archive}")
        if result.warnings:
            parts.append("Avertissements :\n• " + "\n• ".join(result.warnings))
        QMessageBox.information(self, "Clôture terminée", "\n\n".join(parts))

    def _clear_hbox(self, lay: QHBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _ensure_visible(self, w: QWidget) -> None:
        if self._page_scroll is not None:
            self._page_scroll.ensureWidgetVisible(w)

    def _open_structures_ref(self) -> None:
        if self._navigate_to_page is not None:
            self._navigate_to_page(PAGE_STRUCTURES)

    def _rep_row_bar(self, label: str, count: int, max_c: int, bar_hex: str) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        la = QLabel(_truncate(label, 32))
        la.setStyleSheet("font-size: 12px; color: #424242;")
        nu = QLabel(str(count))
        nu.setStyleSheet("font-size: 12px; font-weight: 600; color: #212121;")
        row.addWidget(la, stretch=1)
        row.addWidget(nu)
        topw = QWidget()
        topw.setLayout(row)
        bar = QProgressBar()
        bar.setRange(0, max(1, max_c))
        bar.setValue(count)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(
            "QProgressBar { border: none; background: #f5f5f5; border-radius: 3px; }"
            f"QProgressBar::chunk {{ background: {bar_hex}; border-radius: 3px; }}"
        )
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        v.addWidget(topw)
        v.addWidget(bar)
        return wrap

    def _rep_card_frame(
        self,
        title: str,
        body: QWidget,
        footer: QWidget | None,
        header_extra: QWidget | None,
    ) -> QFrame:
        card = QFrame()
        card.setObjectName("dashRepCard")
        card.setStyleSheet(
            "QFrame#dashRepCard { background: #ffffff; border: 1px solid #e8e8e8; "
            "border-radius: 12px; }"
        )
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        ht = QLabel(title)
        ht.setStyleSheet("font-size: 13px; font-weight: 600; color: #37474f;")
        head.addWidget(ht, stretch=1)
        if header_extra is not None:
            head.addWidget(header_extra, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #eeeeee; border: none; min-height: 1px; max-height: 1px;")
        v.addWidget(sep)
        v.addWidget(body)
        if footer is not None:
            v.addWidget(footer)
        return card

    def _rep_link_voir_tout(self, slot: Callable[[], None]) -> QLabel:
        lab = QLabel(
            "<a href=\"v\" style=\"color: #f9a825; text-decoration: none; font-weight: 600;\">"
            "Voir tout</a>"
        )
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.linkActivated.connect(lambda _u: slot())
        return lab

    def _fill_repartitions(self, s: DashboardSnapshot) -> None:
        self._clear_hbox(self._rep_cards_lay)
        pos_top = s.positions[:6]
        pos_max = max((c for _, c in pos_top), default=1)
        n_pos_other = max(0, len(s.positions) - 6)
        body_p = QWidget()
        vl_p = QVBoxLayout(body_p)
        vl_p.setContentsMargins(0, 4, 0, 0)
        vl_p.setSpacing(10)
        for lab, cnt in pos_top:
            vl_p.addWidget(self._rep_row_bar(lab, cnt, pos_max, "#ffca28"))
        foot_p: QWidget | None = None
        if n_pos_other > 0:
            foot_p = QLabel(f"+{n_pos_other} autre(s)")
            foot_p.setStyleSheet("color: #212121; font-size: 11px;")
        card_p = self._rep_card_frame(
            "Par position administrative",
            body_p,
            foot_p,
            self._rep_link_voir_tout(lambda: self._ensure_visible(self._table)),
        )
        self._rep_cards_lay.addWidget(card_p, stretch=1)

        grades = list(s.grades_effectifs_detail)
        g_top = grades[:6]
        g_max = max((t[1] for t in g_top), default=1)
        n_g_other = max(0, len(grades) - 6)
        body_g = QWidget()
        vl_g = QVBoxLayout(body_g)
        vl_g.setContentsMargins(0, 4, 0, 0)
        vl_g.setSpacing(10)
        for gcode, cnt, _pct, _ec in g_top:
            vl_g.addWidget(self._rep_row_bar(gcode, cnt, g_max, "#64b5f6"))
        foot_g: QWidget | None = None
        if n_g_other > 0:
            foot_g = QLabel(f"+{n_g_other} autre(s)")
            foot_g.setStyleSheet("color: #212121; font-size: 11px;")
        card_g = self._rep_card_frame(
            "Par grade",
            body_g,
            foot_g,
            self._rep_link_voir_tout(lambda: self._ensure_visible(self._grade_table)),
        )
        self._rep_cards_lay.addWidget(card_g, stretch=1)

        n_struct = s.structures_distinctes_effectifs
        body_s = QWidget()
        hs = QHBoxLayout(body_s)
        hs.setContentsMargins(0, 10, 0, 10)
        val = QLabel(str(n_struct))
        val.setStyleSheet("font-size: 36px; font-weight: 700; color: #212121;")
        hs.addWidget(val, alignment=Qt.AlignmentFlag.AlignVCenter)
        hs.addStretch()
        icon_fr = QFrame()
        icon_fr.setFixedSize(52, 52)
        icon_fr.setStyleSheet("background: #e3f2fd; border-radius: 12px; border: none;")
        il = QVBoxLayout(icon_fr)
        il.setContentsMargins(0, 0, 0, 0)
        ic = QLabel()
        ic.setPixmap(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(28, 28)
        )
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        il.addWidget(ic)
        hs.addWidget(icon_fr, alignment=Qt.AlignmentFlag.AlignVCenter)
        if self._navigate_to_page is not None:
            foot_s = QLabel(
                "<a href=\"g\" style=\"color: #26c6da; text-decoration: none;\">"
                "Gérer les structures →</a>"
            )
            foot_s.setTextFormat(Qt.TextFormat.RichText)
            foot_s.linkActivated.connect(lambda _u: self._open_structures_ref())
        else:
            foot_s = QLabel("Référentiel → Structures (barre latérale)")
            foot_s.setStyleSheet("color: #90a4ae; font-size: 11px;")
        card_s = self._rep_card_frame("Structures", body_s, foot_s, None)
        self._rep_cards_lay.addWidget(card_s, stretch=1)

    def _clear_layout(self, layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _clear_vbox(self, lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._reload_stats(silent=True)

    def _reload_stats(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser les indicateurs",
                "Recharger le tableau de bord depuis la base de données (Access / SQLite selon la configuration) ?",
                default_no=False,
            ):
                return
        s = run_with_progress(
            self,
            "Tableau de bord",
            "Chargement des indicateurs…",
            self._dashboard.snapshot,
        )
        if s is None:
            self._grade_table.setRowCount(0)
            self._clear_hbox(self._rep_cards_lay)
            return

        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self._clear_layout(self._pos_grid)
        self._clear_layout(self._charts_layout)
        self._clear_vbox(self._sme_trend_lay)

        if s.sme_table_ok:
            self._sme_hint.setText(
                "Historique des périodes SME enregistrées dans <b>TabSme</b>. "
                "Le graphique de tendance encode le champ <b>Statut</b> (ouvert/actif → 1, fermé → 0, "
                "sinon valeur numérique ou 0,5 par défaut)."
            )
            self._sme_table.setRowCount(len(s.sme_rows))
            for row, r in enumerate(s.sme_rows):
                self._sme_table.setItem(row, 0, QTableWidgetItem(r.codsme))
                self._sme_table.setItem(row, 1, QTableWidgetItem(r.mois))
                self._sme_table.setItem(row, 2, QTableWidgetItem(r.statut))
            self._sme_table.resizeColumnsToContents()
            trend_list = list(s.sme_trend_points)
            self._sme_trend_lay.addWidget(
                _sme_trend_line(
                    trend_list,
                    "Tendance du Statut SME (liste ordonnée par mois)",
                )
            )
        else:
            self._sme_hint.setText(
                "Table <b>TabSme</b> absente ou inaccessible : créez-la dans Access "
                "(colonnes typiques : Codsme, mois, Statut), ou importez le bundle SQLite depuis Access "
                "(page <b>Paramètres</b> ou menu Fichier) pour remplir <b>tab_sme</b> en mode local."
            )
            self._sme_table.setRowCount(0)

        for i, kpi in enumerate(s.main_kpi_cards):
            self._grid.addWidget(_dash_kpi_card(self, kpi), i // 4, i % 4)

        self._fill_repartitions(s)

        for i, (pos, n) in enumerate(s.positions):
            row, col = divmod(i, 4)
            self._pos_grid.addWidget(_pos_card(pos, str(n)), row, col)

        self._charts_layout.addWidget(_pie_presence(s.presents, s.indisponibles), 0, 0)
        self._charts_layout.addWidget(
            _hbar_grouped_multi(s.positions, "Positions administratives (effectifs)"),
            0,
            1,
        )
        self._charts_layout.addWidget(
            _bar_vertical_multi(
                s.structures_distribution,
                "Top structures (effectifs dans la liste générale)",
            ),
            1,
            0,
        )
        self._charts_layout.addWidget(
            _hbar_named_multi(
                s.grades_distribution,
                "Répartition par grade (effectifs)",
            ),
            1,
            1,
        )

        self._table.setRowCount(len(s.positions))
        for row, (pos, n) in enumerate(s.positions):
            self._table.setItem(row, 0, QTableWidgetItem(pos))
            self._table.setItem(row, 1, QTableWidgetItem(str(n)))
        self._table.resizeColumnsToContents()

        self._grade_table.setRowCount(len(s.grades_effectifs_detail))
        for row, (g, n, pct, ecart) in enumerate(s.grades_effectifs_detail):
            self._grade_table.setItem(row, 0, QTableWidgetItem(g))
            self._grade_table.setItem(row, 1, QTableWidgetItem(str(n)))
            self._grade_table.setItem(row, 2, QTableWidgetItem(f"{pct:.1f} %"))
            self._grade_table.setItem(row, 3, QTableWidgetItem(f"{ecart:+.1f} pts"))
        self._grade_table.resizeColumnsToContents()

        tpl = self._settings.template_liste_present_path
        tpl_txt = str(tpl) if tpl else "(aucune — placez resultat.xlsx dans data/ ou Impression/)"
        tnum = self._settings.template_numerique_path
        tnum_txt = (
            str(tnum)
            if tnum
            else "(aucune — placez numerique.xlsx dans data/templates/ ou Impression/)"
        )
        tdet = self._settings.template_detaches_path
        tdet_txt = (
            str(tdet)
            if tdet
            else "(aucune — placez DETACHES.xlsx ou configurez le chemin)"
        )
        lay_txt = str(liste_layout_config_path().resolve())
        db = Path(self._settings.database_path).resolve()

        self._refresh_sme_history_label_only()

        sql_p = self._settings.sqlite_liste_path
        sql_mode = "SQLite" if self._settings.use_sqlite_liste_generale else "Access (ListeGenerale)"
        sql_detail = (
            "données opérationnelles : liste, référentiels, TabSme, satellites (Access sert aux imports)"
            if self._settings.use_sqlite_liste_generale
            else "liste générale sur Access ; référentiels et satellites sur la même base"
        )
        sql_path_txt = html_escape(str(sql_p.resolve())) if sql_p else "(non défini)"
        sql_caption = f"<i>Mode : {sql_mode}</i> — {sql_detail}."
        acc_title = (
            "Access (import depuis l’ancien .mdb)"
            if self._settings.use_sqlite_liste_generale
            else "Access (base principale — mode historique)"
        )
        texts = [
            f"<b>{acc_title}</b><br/>{html_escape(str(db))}",
            f"<b>Fichier SQLite (données principales)</b><br/>{sql_path_txt}<br/>{sql_caption}",
            f"<b>Maquette Excel (liste des présents)</b><br/>{html_escape(tpl_txt)}",
            f"<b>Gabarit Excel (situation numérique)</b><br/>{html_escape(tnum_txt)}",
            f"<b>Fichier officiel détachés</b><br/>{html_escape(tdet_txt)}",
            f"<b>Configuration mise en page (JSON)</b><br/>{html_escape(lay_txt)}",
            "Barre latérale : effectifs, détachés, situation numérique, référentiels.",
        ]
        for lab, txt in zip(self._path_labels, texts):
            lab.setText(txt)


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
