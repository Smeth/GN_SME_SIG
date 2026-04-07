"""Vue Accueil — tableau de bord (synthèse effectifs, graphiques)."""

from __future__ import annotations

from pathlib import Path

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
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings, liste_layout_config_path
from sme_gend.services.dashboard_service import DashboardService


def _card(title: str, value: str, accent: bool = False) -> QFrame:
    f = QFrame()
    f.setObjectName("dashCard")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(20, 16, 20, 20)
    t = QLabel(title)
    t.setObjectName("dashCardTitle")
    t.setWordWrap(True)
    v = QLabel(value)
    v.setObjectName("dashCardValue")
    if accent:
        v.setStyleSheet("color: #c62828;")
    lay.addWidget(t)
    lay.addWidget(v)
    lay.addStretch()
    return f


def _pos_card(label: str, count: str) -> QFrame:
    """Carte compacte pour une position administrative."""
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
    f = QFont("Segoe UI", 9)
    chart.setTitleFont(QFont("Segoe UI", 11, QFont.Weight.DemiBold))


def _make_chart_view(chart: QChart) -> QChartView:
    view = QChartView(chart)
    view.setRenderHint(QPainter.RenderHint.Antialiasing)
    view.setMinimumHeight(280)
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
    return view


def _pie_presence(presents: int, hors: int) -> QChartView:
    series = QPieSeries()
    # Trou du donut : fraction 0–1 (API Qt Charts : setHoleSize, pas setHoleRadius).
    series.setHoleSize(0.45)
    series.setPieSize(0.85)
    if presents > 0:
        sl = series.append("Présents", float(presents))
        sl.setBrush(QColor("#1a237e"))
        sl.setLabelColor(QColor("#ffffff"))
    if hors > 0:
        sl = series.append("Hors « présent »", float(hors))
        sl.setBrush(QColor("#c62828"))
        sl.setLabelColor(QColor("#ffffff"))
    if presents == 0 and hors == 0:
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


def _hbar_positions(positions: list[tuple[str, int]], title: str) -> QChartView:
    top = positions[:14]
    if not top:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    series = QHorizontalBarSeries()
    bar_set = QBarSet("Effectif")
    labels: list[str] = []
    for name, n in reversed(top):
        bar_set << float(n)
        labels.append(_truncate(name, 36))
    series.append(bar_set)
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


def _hbar_named(items: list[tuple[str, int]], title: str, axis_label: str) -> QChartView:
    if not items:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    series = QHorizontalBarSeries()
    bar_set = QBarSet(axis_label)
    labels: list[str] = []
    for _name, n in reversed(items):
        bar_set << float(n)
        labels.append(_truncate(_name, 32))
    bar_set.setBrush(QColor("#1a237e"))
    series.append(bar_set)
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


def _bar_vertical_compare(items: list[tuple[str, int]], title: str) -> QChartView:
    """Histogramme vertical pour comparer les effectifs (structures / grades)."""
    if not items:
        chart = QChart()
        chart.setTitle(title)
        _chart_theme(chart)
        return _make_chart_view(chart)
    series = QBarSeries()
    bar_set = QBarSet("Effectif")
    labels: list[str] = []
    for name, n in items:
        bar_set << float(n)
        labels.append(_truncate(name, 12))
    bar_set.setBrush(QColor("#3949ab"))
    series.append(bar_set)
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


class HomeView(QWidget):
    """Récapitulatif global : effectifs, positions, graphiques, chemins système."""

    def __init__(self, settings: Settings, dashboard: DashboardService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._dashboard = dashboard

        title = QLabel("Tableau de bord")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Indicateurs issus de la liste générale : effectifs, positions administratives, "
            "structures et grades."
        )
        sub.setObjectName("dashSubtitle")
        sub.setWordWrap(True)

        self._btn_refresh = QPushButton("Actualiser les indicateurs")
        self._btn_refresh.setObjectName("dashRefresh")
        self._btn_refresh.clicked.connect(self._reload_stats)

        head = QHBoxLayout()
        head.addWidget(title, alignment=Qt.AlignmentFlag.AlignLeft)
        head.addStretch()
        head.addWidget(self._btn_refresh)

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
        for _ in range(4):
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
        inner_lay.addLayout(self._grid)
        inner_lay.addWidget(
            QLabel(
                "<b>Effectifs par position administrative</b> — une carte par libellé de position",
                textFormat=Qt.TextFormat.RichText,
            )
        )
        inner_lay.addWidget(self._pos_scroll)
        inner_lay.addWidget(
            QLabel("<b>Graphiques</b> — présence, positions, structures, grades", textFormat=Qt.TextFormat.RichText)
        )
        inner_lay.addWidget(self._charts_wrap)
        inner_lay.addWidget(QLabel("<b>Tableau détaillé (positions)</b>", textFormat=Qt.TextFormat.RichText))
        inner_lay.addWidget(self._table)
        inner_lay.addWidget(paths)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

        self._reload_stats()

    def _clear_layout(self, layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _reload_stats(self) -> None:
        try:
            s = self._dashboard.snapshot()
        except Exception as e:
            for lab in self._path_labels:
                lab.setText("")
            if self._path_labels:
                self._path_labels[0].setText(f"<b>Erreur</b><br/>{e}")
            return

        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        self._clear_layout(self._pos_grid)
        self._clear_layout(self._charts_layout)

        c1 = _card("Effectifs enregistrés", str(s.total_effectifs))
        c2 = _card("Présents", str(s.presents))
        c3 = _card("Hors « présent »", str(s.hors_present), accent=True)
        c4 = _card("Structures (liste générale)", str(s.structures_distinctes_effectifs))
        c5 = _card("Structures (référentiel)", str(s.nb_structures_referentiel))
        c6 = _card("Grades (référentiel)", str(s.nb_grades_referentiel))
        c7 = _card("Subdivisions (hiérarchie)", str(s.nb_subdivisions_hierarchie))
        c8 = _card("Taux de présence", f"{s.taux_presence_pct:.1f} %")

        for i, c in enumerate([c1, c2, c3, c4, c5, c6, c7, c8]):
            self._grid.addWidget(c, i // 4, i % 4)

        for i, (pos, n) in enumerate(s.positions):
            row, col = divmod(i, 4)
            self._pos_grid.addWidget(_pos_card(pos, str(n)), row, col)

        self._charts_layout.addWidget(_pie_presence(s.presents, s.hors_present), 0, 0)
        self._charts_layout.addWidget(
            _hbar_positions(s.positions, "Positions administratives (effectifs)"),
            0,
            1,
        )
        self._charts_layout.addWidget(
            _bar_vertical_compare(
                s.structures_distribution,
                "Top structures (effectifs dans la liste générale)",
            ),
            1,
            0,
        )
        self._charts_layout.addWidget(
            _hbar_named(
                s.grades_distribution,
                "Répartition par grade (effectifs)",
                "Personnes",
            ),
            1,
            1,
        )

        self._table.setRowCount(len(s.positions))
        for row, (pos, n) in enumerate(s.positions):
            self._table.setItem(row, 0, QTableWidgetItem(pos))
            self._table.setItem(row, 1, QTableWidgetItem(str(n)))
        self._table.resizeColumnsToContents()

        tpl = self._settings.template_liste_present_path
        tpl_txt = str(tpl) if tpl else "(aucune — placez resultat.xlsx dans data/ ou Impression/)"
        lay_txt = str(liste_layout_config_path().resolve())
        db = Path(self._settings.database_path).resolve()

        texts = [
            f"<b>Base Access</b><br/>{html_escape(str(db))}",
            f"<b>Maquette Excel (liste des présents)</b><br/>{html_escape(tpl_txt)}",
            f"<b>Configuration mise en page (JSON)</b><br/>{html_escape(lay_txt)}",
            "Utilisez la barre latérale pour la liste générale, la mise en page des sections et l’export liste des présents.",
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
