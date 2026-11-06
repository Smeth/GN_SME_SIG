"""Comportement du panneau du tableau de bord."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import (
    DashboardKpiCard,
    DashboardSnapshot,
)
from sme_gend.ui.views.dashboard_stats_panel import (
    DashboardStatsPanel,
    _bar_vertical_multi,
    _hbar_grouped_multi,
    _hbar_named_multi,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        total_effectifs=2,
        presents=1,
        indisponibles=1,
        taux_presence_pct=50.0,
        main_kpi_cards=(
            DashboardKpiCard("effectifs", "Effectifs", "2", None, "default"),
        ),
        positions=[("Présent", 1), ("Malade", 1)],
        structures_distinctes_effectifs=1,
        nb_structures_referentiel=1,
        nb_grades_referentiel=1,
        nb_subdivisions_hierarchie=0,
        grades_distribution=[("GND", 2)],
        structures_distribution=[("U001", 2)],
        grades_effectifs_detail=(("GND", 2, 100.0, 0.0),),
        sme_table_ok=False,
        sme_rows=(),
        sme_trend_points=(),
        gain_perte_cards=(
            DashboardKpiCard("gains_mois", "Gains du mois", "1", "mois 2026-05", "success"),
        ),
        gain_perte_mois="2026-05",
        view_mois="2026-04",
        is_archive_view=True,
    )


def test_gain_perte_cards_navigate_to_the_month_they_display(qapp) -> None:
    navigations: list[tuple[str, str | None]] = []
    panel = DashboardStatsPanel(
        Settings(database_path=Path(".")),
        navigate_gain_perte=lambda kind, mois_ym=None: navigations.append((kind, mois_ym)),
    )
    panel.update(_snapshot())
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()

    card = panel._gp_grid.itemAt(0).widget()
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)

    assert navigations == [("gains_mois", "2026-05")]
    assert "2026-05" in panel._gp_sep_lbl.text()

    panel.clear()
    assert panel._grid.count() == 0
    assert panel._charts_layout.count() == 0
    assert panel._pos_grid.count() == 0
    assert panel._gp_grid.count() == 0
    assert panel._table.rowCount() == 0
    assert panel._grade_table.rowCount() == 0
    assert not panel._gp_block.isVisible()
    panel.close()


@pytest.mark.parametrize(
    ("build_chart", "items", "expected_values"),
    [
        (_hbar_grouped_multi, [("A", 4), ("B", 7)], [7.0, 4.0]),
        (_bar_vertical_multi, [("A", 4), ("B", 7)], [4.0, 7.0]),
        (_hbar_named_multi, [("A", 4), ("B", 7)], [7.0, 4.0]),
    ],
)
def test_distribution_charts_use_one_labeled_series_with_all_values(
    qapp, build_chart, items, expected_values
) -> None:
    view = build_chart(items, "Répartition")
    series = view.chart().series()

    assert len(series) == 1
    bar_sets = series[0].barSets()
    assert len(bar_sets) == 1
    assert bar_sets[0].label() == "Effectif"
    assert [bar_sets[0].at(i) for i in range(bar_sets[0].count())] == expected_values
    view.close()
