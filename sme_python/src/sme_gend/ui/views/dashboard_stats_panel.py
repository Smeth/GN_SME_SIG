"""Panneau tableau de bord statistiques (KPIs, graphiques, positions, grades)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QChart,
    QChartView,
    QHorizontalBarSeries,
    QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import DashboardKpiCard, DashboardSnapshot
from sme_gend.ui.theme import theme_palette
from sme_gend.ui.navigation_pages import (
    PAGE_CAREER_ALERTS,
    PAGE_DETACHES,
    PAGE_EDITION_SME,
    PAGE_GAIN_PERTE,
    PAGE_LISTE_GENERALE,
    PAGE_MALADE_PUNIS,
    PAGE_NUMERIQUE,
    PAGE_STAGES,
    PAGE_MISSIONS,
    PAGE_DESERTION,
    PAGE_CONGES,
    PAGE_STRUCTURES,
)

# Consultés en premier au quotidien — mis en avant en tête du tableau de bord,
# le reste des positions administratives est replié dans une section secondaire.
_ESSENTIAL_KPI_IDS: tuple[str, ...] = (
    "effectifs", "present", "taux", "detache", "malade", "career_alerts",
)

_KPI_PAGE_MAP: dict[str, int] = {
    "effectifs": PAGE_LISTE_GENERALE,
    "present": PAGE_LISTE_GENERALE,
    "detache": PAGE_DETACHES,
    "structure_detache": PAGE_DETACHES,
    "mission": PAGE_MISSIONS,
    "malade": PAGE_MALADE_PUNIS,
    "deserteur": PAGE_DESERTION,
    "stage_local": PAGE_STAGES,
    "stage_ex": PAGE_STAGES,
    "stage_interne": PAGE_STAGES,
    "punis": PAGE_MALADE_PUNIS,
    "conge_admin": PAGE_CONGES,
    "deces": PAGE_GAIN_PERTE,
    "gains_mois": PAGE_GAIN_PERTE,
    "pertes_mois": PAGE_GAIN_PERTE,
    "career_alerts": PAGE_CAREER_ALERTS,
    "conge_long_alert": PAGE_CONGES,
}

_KPI_ICON_META: dict[str, tuple[QStyle.StandardPixmap, str]] = {
    "effectifs": (QStyle.StandardPixmap.SP_DriveHDIcon, "#eceff1"),
    "present": (QStyle.StandardPixmap.SP_DialogApplyButton, "#e8f5e9"),
    "detache": (QStyle.StandardPixmap.SP_ArrowLeft, "#e3f2fd"),
    "structure_detache": (QStyle.StandardPixmap.SP_DirIcon, "#e8eaf6"),
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
    # Gains & Pertes (gains & pertes)
    "deces": (QStyle.StandardPixmap.SP_MessageBoxCritical, "#ffebee"),
    "gains_mois": (QStyle.StandardPixmap.SP_ArrowDown, "#e8f5e9"),
    "pertes_mois": (QStyle.StandardPixmap.SP_ArrowUp, "#fff8e1"),
    "career_alerts": (QStyle.StandardPixmap.SP_MessageBoxWarning, "#ffe0b2"),
    "conge_long_alert": (QStyle.StandardPixmap.SP_MessageBoxWarning, "#ffccbc"),
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
    pix_kind, bg = _KPI_ICON_META.get(kpi_id, (QStyle.StandardPixmap.SP_FileIcon, "#eceff1"))
    pm = parent.style().standardIcon(pix_kind).pixmap(22, 22)
    box = QFrame()
    box.setFixedSize(44, 44)
    box.setStyleSheet(f"background-color: {bg}; border-radius: 10px; border: none;")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    ic = QLabel()
    ic.setPixmap(pm)
    ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay.addWidget(ic)
    return box


def _dash_kpi_card(
    parent: QWidget,
    card: DashboardKpiCard,
    *,
    on_click: Callable[[], None] | None = None,
    emphasized: bool = False,
) -> QFrame:
    pal = theme_palette()
    f = QFrame()
    f.setObjectName("dashKpiCard")
    if card.variant == "alert":
        frame_ss = (
            f"QFrame#dashKpiCard {{ background: {pal['bg_card']}; border-radius: 10px; "
            f"border: 1px solid {pal['error']}; border-left: 4px solid {pal['error']}; }}"
        )
    elif card.variant == "highlight":
        frame_ss = (
            f"QFrame#dashKpiCard {{ background: {pal['bg_elevated']}; border-radius: 10px; "
            f"border: 1px solid {pal['warning']}; }}"
        )
    else:
        frame_ss = (
            f"QFrame#dashKpiCard {{ background: {pal['bg_card']}; border-radius: 10px; "
            f"border: 1px solid {pal['border']}; }}"
        )
    f.setStyleSheet(frame_ss)
    if on_click is not None:
        f.setCursor(Qt.CursorShape.PointingHandCursor)
        f.setToolTip("Cliquer pour ouvrir la vue détaillée")

        def _release(ev):
            if ev.button() == Qt.MouseButton.LeftButton:
                on_click()
            QFrame.mouseReleaseEvent(f, ev)

        f.mouseReleaseEvent = _release  # type: ignore[method-assign]
    outer = QVBoxLayout(f)
    outer.setContentsMargins(16, 14, 16, 14)
    outer.setSpacing(4)
    top = QHBoxLayout()
    top.setSpacing(8)
    title = QLabel(card.title)
    title.setObjectName("dashKpiCardTitle")
    title.setWordWrap(True)
    title.setStyleSheet(f"color: {pal['text_muted']}; font-size: 12px;")
    top.addWidget(title, stretch=1, alignment=Qt.AlignmentFlag.AlignTop)
    top.addWidget(_kpi_icon_badge(parent, card.kpi_id), alignment=Qt.AlignmentFlag.AlignTop)
    outer.addLayout(top)
    val = QLabel(card.value)
    val.setObjectName("dashKpiCardValue")
    vc = _kpi_value_color(card.variant)
    val_size = 28 if emphasized else 22
    if vc:
        val.setStyleSheet(
            f"color: {vc}; font-weight: 600; font-size: {val_size}px; font-family: 'Segoe UI', sans-serif;"
        )
    else:
        val.setStyleSheet(
            f"color: {pal['text']}; font-weight: 600; font-size: {val_size}px; "
            "font-family: 'Segoe UI', sans-serif;"
        )
    outer.addWidget(val)
    if card.subtitle:
        sub = QLabel(card.subtitle)
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {pal['text_dim']}; font-size: 11px;")
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
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _chart_theme(chart: QChart) -> None:
    pal = theme_palette()
    chart.setBackgroundVisible(True)
    chart.setBackgroundBrush(QColor(pal["chart_bg"]))
    chart.setTitleBrush(QColor(pal["text"]))
    leg = chart.legend()
    leg.setVisible(True)
    leg.setAlignment(Qt.AlignmentFlag.AlignBottom)
    leg.setLabelColor(QColor(pal["chart_label"]))
    chart.setTitleFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))


def _make_chart_view(chart: QChart) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setFixedHeight(300)
    view.setMinimumWidth(0)
    view.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.MinimumExpanding)
    return view


def _pie_presence(presents: int, indisponibles: int) -> QChartView:
    series = QPieSeries()
    series.setHoleSize(0.45)
    series.setPieSize(0.85)
    if presents > 0:
        sl = series.append("Présents", float(presents))
        sl.setBrush(QColor("#1a237e"))
        sl.setLabelColor(QColor(theme_palette()["chart_label"]))
    if indisponibles > 0:
        sl = series.append("Indisponibles", float(indisponibles))
        sl.setBrush(QColor("#c62828"))
        sl.setLabelColor(QColor(theme_palette()["chart_label"]))
    for s in series.slices():
        s.setLabelVisible(False)
        s.setLabel(f"{s.label()} : {int(s.value())}")
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle("Répartition présence (liste générale)")
    _chart_theme(chart)
    if presents == 0 and indisponibles == 0:
        chart.setTitle('Présence — aucune donnée')
        chart.legend().setVisible(False)
    chart.legend().setAlignment(Qt.AlignmentFlag.AlignRight)
    return _make_chart_view(chart)


def _hbar_grouped_multi(items: list[tuple[str, int]], title: str) -> QChartView:
    top = items[:14]
    if not top:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    rev = list(reversed(top))
    series = QHorizontalBarSeries()
    bars = QBarSet("Effectif")
    for _, val in rev:
        bars << float(val)
    bars.setBrush(QColor("#3949ab"))
    series.append(bars)
    labels = [_truncate(name, 36) for name, _ in rev]
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
    if not items:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    series = QBarSeries()
    bars = QBarSet("Effectif")
    for _, val in items:
        bars << float(val)
    bars.setBrush(QColor("#00897b"))
    series.append(bars)
    labels = [_truncate(name, 12) for name, _ in items]
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
    series = QHorizontalBarSeries()
    bars = QBarSet("Effectif")
    for _, val in rev:
        bars << float(val)
    bars.setBrush(QColor("#7986cb"))
    series.append(bars)
    labels = [_truncate(name, 32) for name, _ in rev]
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


class DashboardStatsPanel(QWidget):
    """Panneau KPIs, graphiques, positions et grades (données DashboardService)."""

    def __init__(
        self,
        settings: Settings,
        *,
        navigate_to_page: Callable[[int], None] | None = None,
        navigate_gain_perte: Callable[[str, str | None], None] | None = None,
        ensure_visible: Callable[[QWidget], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._navigate_to_page = navigate_to_page
        self._navigate_gain_perte = navigate_gain_perte
        self._ensure_visible_cb = ensure_visible
        self._last_view_mois: str | None = None

        lay = QVBoxLayout(self)
        lay.setSpacing(20)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(
            QLabel(
                "<b>Indicateurs principaux</b> — effectifs et positions administratives "
                "(ListeGenerale.Padmin), pour le mois affiché.",
                textFormat=Qt.TextFormat.RichText,
            )
        )

        self._grid = QGridLayout()
        self._grid.setSpacing(16)
        lay.addLayout(self._grid)

        # Positions secondaires — repliable, dépliée par défaut (rien ne disparaît
        # silencieusement, juste moins mis en avant que les KPI essentiels ci-dessus).
        self._secondary_toggle = QToolButton()
        self._secondary_toggle.setCheckable(True)
        self._secondary_toggle.setChecked(True)
        self._secondary_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._secondary_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self._secondary_toggle.setStyleSheet("QToolButton { border: none; font-size: 12px; }")
        self._secondary_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(self._secondary_toggle)

        self._secondary_block = QWidget()
        secondary_outer = QVBoxLayout(self._secondary_block)
        secondary_outer.setContentsMargins(0, 0, 0, 0)
        self._secondary_grid = QGridLayout()
        self._secondary_grid.setSpacing(16)
        secondary_outer.addLayout(self._secondary_grid)
        lay.addWidget(self._secondary_block)

        def _toggle_secondary(checked: bool) -> None:
            self._secondary_block.setVisible(checked)
            self._secondary_toggle.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )

        self._secondary_toggle.toggled.connect(_toggle_secondary)

        # Section Gains & Pertes (masquée si aucune donnée)
        self._gp_block = QWidget()
        self._gp_block.setVisible(False)
        _gp_outer = QVBoxLayout(self._gp_block)
        _gp_outer.setSpacing(8)
        _gp_outer.setContentsMargins(0, 8, 0, 0)
        self._gp_sep_lbl = QLabel(
            "<b>Gains &amp; Pertes — mois actif</b> "
            "(gains & pertes : mutations entrantes, sorties, décès)",
            textFormat=Qt.TextFormat.RichText,
        )
        _gp_outer.addWidget(self._gp_sep_lbl)
        self._gp_grid = QGridLayout()
        self._gp_grid.setSpacing(16)
        _gp_outer.addLayout(self._gp_grid)
        lay.addWidget(self._gp_block)

        self._repartitions_block = QWidget()
        rep_outer = QVBoxLayout(self._repartitions_block)
        rep_outer.setSpacing(14)
        rep_outer.setContentsMargins(0, 8, 0, 0)
        self._rep_title = QLabel("RÉPARTITIONS")
        pal = theme_palette()
        self._rep_title.setStyleSheet(
            f"color: {pal['text']}; font-size: 12px; font-weight: 700; letter-spacing: 4px;"
        )
        self._rep_cards_row = QWidget()
        self._rep_cards_lay = QGridLayout(self._rep_cards_row)
        self._rep_cards_lay.setSpacing(16)
        self._rep_cards_lay.setContentsMargins(0, 0, 0, 0)
        rep_outer.addWidget(self._rep_title)
        rep_outer.addWidget(self._rep_cards_row)
        lay.addWidget(self._repartitions_block)

        lay.addWidget(
            QLabel(
                "<b>Effectifs par position administrative</b> — une carte par libellé",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        self._pos_scroll = QScrollArea()
        self._pos_scroll.setObjectName("dashPosScroll")
        self._pos_scroll.setWidgetResizable(True)
        self._pos_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._pos_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pos_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pos_inner = QWidget()
        self._pos_grid = QGridLayout(self._pos_inner)
        self._pos_grid.setSpacing(12)
        self._pos_scroll.setWidget(self._pos_inner)
        lay.addWidget(self._pos_scroll)

        lay.addWidget(
            QLabel(
                "<b>Graphiques et courbes</b> — présence, positions, structures, grades",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        self._charts_wrap = QWidget()
        self._charts_layout = QGridLayout(self._charts_wrap)
        self._charts_layout.setSpacing(16)
        lay.addWidget(self._charts_wrap)

        lay.addWidget(
            QLabel(
                "<b>Effectifs et répartition par grade</b> — variations par rapport à une base uniforme",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        self._grade_hint = QLabel(
            "Pour chaque libellé de grade : effectif, part du personnel total, et "
            "<b>écart en points de pourcentage</b> par rapport à une répartition <i>uniforme</i> "
            "(si tous les grades observés avaient le même effectif)."
        )
        self._grade_hint.setWordWrap(True)
        self._grade_hint.setObjectName("dashSubtitle")
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
        self._grade_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._grade_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay.addWidget(self._grade_hint)
        lay.addWidget(self._grade_table)

        lay.addWidget(
            QLabel("<b>Tableau détaillé (positions)</b>", textFormat=Qt.TextFormat.RichText)
        )
        self._table = QTableWidget()
        self._table.setColumnCount(2)
        self._table.setHorizontalHeaderLabels(["Position administrative", "Effectif"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay.addWidget(self._table)

    # ── Public API ────────────────────────────────────────────────────────────

    def update(self, s: DashboardSnapshot) -> None:
        """Remplit tous les widgets depuis un DashboardSnapshot."""
        from sme_gend.services.dashboard_snapshot_compat import coerce_dashboard_snapshot

        s = coerce_dashboard_snapshot(s)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._clear_grid(self._secondary_grid)

        self._clear_grid(self._pos_grid)
        self._clear_grid(self._charts_layout)

        essential = [k for kid in _ESSENTIAL_KPI_IDS for k in s.main_kpi_cards if k.kpi_id == kid]
        secondary = [k for k in s.main_kpi_cards if k.kpi_id not in _ESSENTIAL_KPI_IDS]

        def _make_card(kpi: DashboardKpiCard, *, emphasized: bool) -> QFrame:
            page = _KPI_PAGE_MAP.get(kpi.kpi_id)
            on_click = None
            if self._navigate_to_page and page is not None:
                if kpi.kpi_id == "conge_long_alert":
                    def _open_conges_alerts(p=page) -> None:
                        from sme_gend.ui.views.conges_view import CongesView

                        CongesView.request_alerts_tab()
                        self._navigate_to_page(p)

                    on_click = _open_conges_alerts
                else:
                    on_click = lambda p=page: self._navigate_to_page(p)
            return _dash_kpi_card(self, kpi, on_click=on_click, emphasized=emphasized)

        for i, kpi in enumerate(essential):
            self._grid.addWidget(_make_card(kpi, emphasized=True), i // 4, i % 4)

        for i, kpi in enumerate(secondary):
            self._secondary_grid.addWidget(_make_card(kpi, emphasized=False), i // 4, i % 4)

        self._secondary_toggle.setText(f"Indisponibles ({len(secondary)})")

        # Gains & Pertes
        self._clear_grid(self._gp_grid)
        self._last_view_mois = (s.gain_perte_mois or "").strip() or None
        if s.gain_perte_cards:
            month_label = f" — {self._last_view_mois}" if self._last_view_mois else ""
            self._gp_sep_lbl.setText(
                "<b>Gains &amp; Pertes — mois actif"
                f"{month_label}</b> (gains & pertes : mutations entrantes, sorties, décès)"
            )
            for i, kpi in enumerate(s.gain_perte_cards):
                on_click = None
                if self._navigate_gain_perte is not None:
                    kpi_id = kpi.kpi_id
                    mois = self._last_view_mois

                    def _gp_nav(k=kpi_id, m=mois) -> None:
                        self._navigate_gain_perte(k, mois_ym=m)

                    on_click = _gp_nav
                self._gp_grid.addWidget(
                    _dash_kpi_card(self, kpi, on_click=on_click),
                    0,
                    i,
                )
            self._gp_block.setVisible(True)
        else:
            self._gp_block.setVisible(False)

        self._fill_repartitions(s)

        for i, (pos, n) in enumerate(s.positions):
            row, col = divmod(i, 4)
            self._pos_grid.addWidget(_pos_card(pos, str(n)), row, col)

        self._charts_layout.addWidget(_pie_presence(s.presents, s.indisponibles), 0, 0)
        self._charts_layout.addWidget(
            _hbar_grouped_multi(s.positions, "Positions administratives (effectifs)"), 0, 1,
        )
        self._charts_layout.addWidget(
            _bar_vertical_multi(
                s.structures_distribution,
                "Top structures (effectifs dans la liste générale)",
            ),
            1, 0,
        )
        self._charts_layout.addWidget(
            _hbar_named_multi(s.grades_distribution, "Répartition par grade (effectifs)"), 1, 1,
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
        self._fit_table_height(self._grade_table)
        self._fit_table_height(self._table)
        self._fit_pos_scroll()
        self._reflow_cards()

    @staticmethod
    def _reflow_grid(layout, columns):
        widgets = []
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                widgets.append(item.widget())
        for column in range(max(4, layout.columnCount())):
            layout.setColumnStretch(column, 1 if column < columns else 0)
        for index, widget in enumerate(widgets):
            layout.addWidget(widget, index // columns, index % columns)

    def _reflow_cards(self):
        width = self.width()
        cards = max(1, min(4, (width + 16) // 260))
        charts = 2 if width >= 1000 else 1
        repartitions = 3 if width >= 1080 else 2 if width >= 720 else 1
        for layout in (self._grid, self._secondary_grid, self._gp_grid, self._pos_grid):
            self._reflow_grid(layout, cards)
        self._reflow_grid(self._charts_layout, charts)
        self._reflow_grid(self._rep_cards_lay, repartitions)
        self._position_columns = cards
        self._fit_pos_scroll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_cards()

    def clear(self) -> None:
        self._clear_grid(self._grid)
        self._clear_grid(self._secondary_grid)
        self._clear_grid(self._gp_grid)
        self._clear_grid(self._pos_grid)
        self._clear_grid(self._charts_layout)
        self._grade_table.setRowCount(0)
        self._table.setRowCount(0)
        self._clear_hbox(self._rep_cards_lay)
        self._gp_block.setVisible(False)
        self._last_view_mois = None
        self._secondary_toggle.setText("Indisponibles (0)")

    def _fit_table_height(self, table: QTableWidget) -> None:
        table.resizeRowsToContents()
        header_h = table.horizontalHeader().height()
        rows_h = sum(table.rowHeight(i) for i in range(table.rowCount()))
        height = header_h + rows_h + table.frameWidth() * 2 + 6
        table.setMinimumHeight(max(height, header_h + 8))
        table.setMaximumHeight(max(height, header_h + 8))

    def _fit_pos_scroll(self) -> None:
        count = self._pos_grid.count()
        columns = getattr(self, '_position_columns', 4)
        rows = max(1, (count + columns - 1) // columns) if count else 1
        height = rows * 96 + 8
        self._pos_scroll.setMinimumHeight(height)
        self._pos_scroll.setMaximumHeight(height)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ── Private helpers ───────────────────────────────────────────────────────

    def _clear_hbox(self, lay: QHBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _clear_grid(self, layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _ensure_visible(self, w: QWidget) -> None:
        if self._ensure_visible_cb is not None:
            self._ensure_visible_cb(w)

    def _open_structures_ref(self) -> None:
        if self._navigate_to_page is not None:
            self._navigate_to_page(PAGE_STRUCTURES)

    def _rep_row_bar(self, label: str, count: int, max_c: int, bar_hex: str) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        la = QLabel(_truncate(label, 32))
        pal = theme_palette()
        la.setStyleSheet(f"font-size: 12px; color: {pal['text_muted']};")
        nu = QLabel(str(count))
        nu.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {pal['text']};")
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
            f"QProgressBar {{ border: none; background: {pal['bar_bg']}; border-radius: 3px; }}"
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
        pal = theme_palette()
        card.setStyleSheet(
            f"QFrame#dashRepCard {{ background: {pal['bg_card']}; border: 1px solid {pal['border']}; "
            "border-radius: 12px; }"
        )
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        head = QHBoxLayout()
        ht = QLabel(title)
        ht.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {pal['text_muted']};")
        head.addWidget(ht, stretch=1)
        if header_extra is not None:
            head.addWidget(
                header_extra,
                alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
            )
        v.addLayout(head)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            f"background: {pal['border']}; border: none; min-height: 1px; max-height: 1px;"
        )
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
            foot_p.setStyleSheet(
                f"color: {theme_palette()['text']}; font-size: 11px;"
            )
        card_p = self._rep_card_frame(
            "Par position administrative",
            body_p,
            foot_p,
            self._rep_link_voir_tout(lambda: self._ensure_visible(self._table)),
        )
        self._rep_cards_lay.addWidget(card_p, 0, 0)

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
            foot_g.setStyleSheet(
                f"color: {theme_palette()['text']}; font-size: 11px;"
            )
        card_g = self._rep_card_frame(
            "Par grade",
            body_g,
            foot_g,
            self._rep_link_voir_tout(lambda: self._ensure_visible(self._grade_table)),
        )
        self._rep_cards_lay.addWidget(card_g, 0, 1)

        n_struct = s.structures_distinctes_effectifs
        body_s = QWidget()
        hs = QHBoxLayout(body_s)
        hs.setContentsMargins(0, 10, 0, 10)
        val_lbl = QLabel(str(n_struct))
        val_lbl.setStyleSheet(
            f"font-size: 36px; font-weight: 700; color: {theme_palette()['text']};"
        )
        hs.addWidget(val_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        hs.addStretch()
        icon_fr = QFrame()
        icon_fr.setFixedSize(52, 52)
        icon_fr.setStyleSheet(
            f"background: {theme_palette()['bg_elevated']}; border-radius: 12px; border: none;"
        )
        il = QVBoxLayout(icon_fr)
        il.setContentsMargins(0, 0, 0, 0)
        ic = QLabel()
        ic.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon).pixmap(28, 28))
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
        self._rep_cards_lay.addWidget(card_s, 0, 2)
