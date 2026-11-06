"""Doublons liste générale après double import."""

from sme_gend.config.liste_generale_scope import carriere_scope_sql


def test_carriere_scope_legacy_skips_when_dated_segment_exists() -> None:
    sql, params = carriere_scope_sql("2026-05")
    assert "NOT EXISTS" in sql
    assert "sme_periode_id IS NOT NULL OR" in sql.replace("\n", " ")
    assert len(params) == 4


def test_carriere_scope_params_order() -> None:
    from datetime import date

    sql, params = carriere_scope_sql("2026-04")
    assert params == ("202604", date(2026,4,30), date(2026,4,30), "202604")
    assert sql.count("%s") == len(params)
