"""Stabilité import : rapport, helpers."""

from __future__ import annotations

from sme_gend.services.import_report import format_import_result_html
from sme_gend.services.import_stability import is_missing_table_error, strip_html_line


def test_format_import_result_html_minimal() -> None:
    html_out = format_import_result_html(
        {
            "sme_mois": "2025-08",
            "historical": True,
            "liste_inserted": 2,
            "liste_updated": 10,
            "conge_padmin_sync": 3,
            "dossier_enrichment": {
                "dossier_diplomes": 1,
                "dossier_passations": 2,
                "dossier_affectations": 3,
            },
            "referentials": {"warnings": ["TabConge absente"]},
        },
        include_alerts=False,
    )
    assert "2025-08" in html_out
    assert "diplôme" in html_out.lower() or "Diplôme" in html_out
    assert "TabConge" in html_out


def test_format_import_result_html_shows_reconciliation_ok() -> None:
    from sme_gend.services.carriere_reconciliation import (
        CarriereReconciliationResult,
        CarriereUnresolvedCounts,
    )

    html_out = format_import_result_html(
        {
            "sme_mois": "2026-05",
            "liste_inserted": 1,
            "liste_updated": 0,
            "carriere_reconciliation": CarriereReconciliationResult(
                fk_rows_updated=120,
                ordre_tri_updated=45,
                unresolved_before=CarriereUnresolvedCounts(grade=3),
                unresolved_after=CarriereUnresolvedCounts(),
                etl_errors_logged=0,
                mois="2026-05",
            ),
        },
        include_alerts=False,
    )
    assert "Réconciliation carrière" in html_out
    assert "120" in html_out
    assert "résolues" in html_out.lower()


def test_format_import_result_html_shows_reconciliation_warning() -> None:
    html_out = format_import_result_html(
        {
            "sme_mois": "2026-05",
            "carriere_reconciliation": {
                "fk_updated": 10,
                "ordre_tri_updated": 2,
                "unresolved_before": 5,
                "unresolved_after": 2,
                "etl_errors_logged": 2,
            },
        },
        include_alerts=False,
    )
    assert "Attention" in html_out
    assert "etl_erreur" in html_out


def test_strip_html_line() -> None:
    assert "Grade" in strip_html_line("• <b>Grade</b> : LT → CNE")


def test_is_missing_table_error() -> None:
    assert is_missing_table_error(Exception("Table 'x.agent_conge' doesn't exist"))
