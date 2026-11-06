"""Compatibilité des instantanés tableau de bord."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services.dashboard_service import DashboardKpiCard
from sme_gend.services.dashboard_snapshot_compat import coerce_dashboard_snapshot
from sme_gend.ui.views.dashboard_stats_panel import DashboardStatsPanel


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def test_coerce_fills_missing_gain_perte_mois() -> None:
    legacy = SimpleNamespace(
        total_effectifs=1,
        presents=1,
        indisponibles=0,
        taux_presence_pct=100.0,
        main_kpi_cards=(),
        positions=[],
        structures_distinctes_effectifs=0,
        nb_structures_referentiel=0,
        nb_grades_referentiel=0,
        nb_subdivisions_hierarchie=0,
        grades_distribution=[],
        structures_distribution=[],
        grades_effectifs_detail=(),
        sme_table_ok=False,
        sme_rows=(),
        sme_trend_points=(),
        gain_perte_cards=(),
    )
    snap = coerce_dashboard_snapshot(legacy)
    assert snap.gain_perte_mois is None
    assert snap.view_mois is None


def test_panel_update_accepts_legacy_snapshot_without_gain_perte_mois(qapp) -> None:
    legacy = SimpleNamespace(
        total_effectifs=2,
        presents=1,
        indisponibles=1,
        taux_presence_pct=50.0,
        main_kpi_cards=(
            DashboardKpiCard("effectifs", "Effectifs", "2", None, "default"),
        ),
        positions=[("Présent", 1)],
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
        gain_perte_cards=(),
    )
    panel = DashboardStatsPanel(Settings(database_path=Path(".")))
    panel.update(legacy)
    panel.resize(800, 600)
    panel.show()
    qapp.processEvents()
    assert panel._grid.count() >= 1
    panel.close()
