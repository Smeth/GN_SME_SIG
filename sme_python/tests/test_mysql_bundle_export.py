"""Tests export MySQL → Access (sans ODBC)."""

from __future__ import annotations

from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.services.export_report import format_export_result_html
from sme_gend.services.mysql_bundle_export_to_access import (
    export_bundle_filename,
    mouvement_to_gain_perte_row,
)


def test_export_bundle_filename() -> None:
    name = export_bundle_filename("2026-05", stamp="20260524_120000")
    assert name == "sme_bundle_2026-05_20260524_120000.mdb"


def test_mouvement_to_gain_perte_row_prefers_categorie() -> None:
    row = (
        "42",
        "LT",
        "DUPONT Jean",
        "GAIN",
        "PERTE",
        "052026",
        "AJOUT",
        "legacy obs",
    )
    gp = mouvement_to_gain_perte_row(row)
    assert isinstance(gp, GainPerteRow)
    assert gp.id == "42"
    assert gp.grade == "LT"
    assert gp.nom == "DUPONT Jean"
    assert gp.objet == "GAIN"
    assert gp.cdsme == "052026"
    assert gp.observ == "AJOUT"


def test_mouvement_to_gain_perte_row_fallback_type() -> None:
    gp = mouvement_to_gain_perte_row(("7", None, None, None, "PERTE", "012026", None, "DECES"))
    assert gp.objet == "PERTE"
    assert gp.observ == "DECES"


def test_format_export_result_html() -> None:
    html_out = format_export_result_html(
        {
            "sme_mois": "2026-05",
            "output_path": r"C:\exports\sme_bundle_2026-05.mdb",
            "counts": {
                "ListeGenerale": 120,
                "TabSme": 24,
                "TabGainPerte": 8,
                "TabMission": 3,
                "TabMalade": 2,
                "TabStageLocal": 1,
                "TabStageEx": 0,
            },
            "new_legacy_ids": 2,
            "warnings": ["TabConge absente"],
        }
    )
    assert "2026-05" in html_out
    assert "120" in html_out
    assert "legacy_access_id" in html_out
    assert "TabConge" in html_out
    assert "ancien SME Access" in html_out
