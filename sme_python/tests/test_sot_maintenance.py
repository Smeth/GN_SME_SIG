"""Tests maintenance SoT (métriques + projection batch)."""

from __future__ import annotations

from sme_gend.services.sot_maintenance import SotMetricsReport


def test_sot_metrics_report_to_dict() -> None:
    r = SotMetricsReport(
        mois_actif="2026-05",
        agents_sans_actuelle=2,
        ecarts_diplome=1,
        ecarts_grade=0,
    )
    d = r.to_dict()
    assert d["mois_actif"] == "2026-05"
    assert d["agents_sans_actuelle"] == 2
    assert d["ecarts_diplome"] == 1
