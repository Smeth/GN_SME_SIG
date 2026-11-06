"""Tests rapport décompte import liste générale."""

from __future__ import annotations

from sme_gend.services.import_report import format_liste_generale_import_stats_html


def test_stats_html_shows_zero_merge() -> None:
    html = format_liste_generale_import_stats_html(
        {
            "rows_read": 100,
            "ids_empty_source": 2,
            "ids_auto_generated": 2,
            "ids_ignored": 0,
            "matricule_merged": 0,
            "matricule_duplicates_kept": 3,
            "agents_distinct": 97,
            "liste_inserted": 10,
            "liste_updated": 90,
        }
    )
    assert "Lignes lues" in html
    assert "Fusionnées par matricule : <b>0</b>" in html
    assert "Agents distincts" in html
    assert "97" in html
