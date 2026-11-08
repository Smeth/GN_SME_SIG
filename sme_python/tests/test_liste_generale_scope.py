"""Tests périmètre liste générale (mois actif)."""

from __future__ import annotations
from datetime import date

from dataclasses import replace
from unittest.mock import patch

from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.config.liste_generale_scope import (
    build_export_mois_context,
    carriere_scope_for_numerique,
    carriere_scope_for_settings,
    carriere_scope_sql,
    resolve_carriere_scope_mois,
    resolve_export_mois,
    resolve_liste_view_mois,
    settings_for_numerique_data,
)


def _settings(**kw) -> Settings:
    base = Settings(database_path=Path("."))
    return replace(base, **kw)


def test_resolve_liste_view_mois_prefers_explicit_archive() -> None:
    st = _settings(mysql_view_mois="2025-06")
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        assert resolve_liste_view_mois(st) == "2025-06"


def test_resolve_liste_view_mois_uses_active_month() -> None:
    st = _settings(mysql_view_mois=None)
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        assert resolve_liste_view_mois(st) == "2026-04"


def test_resolve_export_mois_matches_liste_view() -> None:
    st = _settings(mysql_view_mois="2025-06")
    from sme_gend.config.liste_generale_scope import resolve_export_mois

    assert resolve_export_mois(st) == resolve_liste_view_mois(st)


def test_build_export_mois_context_archive() -> None:
    st = _settings(mysql_view_mois="2025-06")
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        ctx = build_export_mois_context(st)
        assert ctx.export_mois == "2025-06"
        assert ctx.mois_actif == "2026-04"
        assert ctx.is_archive_view is True
        assert "2025-06" in ctx.banner_html()


def test_resolve_carriere_scope_mois_operational_uses_exact_month() -> None:
    st = _settings(mysql_view_mois=None)
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx, patch(
        "sme_gend.config.sme_context.is_mois_ferme", return_value=False
    ):
        load_ctx.return_value.mois_actif = "2026-05"
        assert resolve_liste_view_mois(st) == "2026-05"
        assert resolve_carriere_scope_mois(st) == "2026-05"
        sql, params = carriere_scope_for_settings(st)
        assert "est_actuelle" in sql
        assert params == ("202605", "052026", date(2026,5,31), date(2026,5,31), "202605", "052026")


def test_resolve_carriere_scope_mois_archive_uses_interval() -> None:
    st = _settings(mysql_view_mois="2026-04")
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx, patch(
        "sme_gend.config.sme_context.is_mois_ferme", return_value=False
    ):
        load_ctx.return_value.mois_actif = "2026-05"
        assert resolve_carriere_scope_mois(st) == "2026-04"
        sql, params = carriere_scope_for_settings(st)
        assert "date_debut" in sql
        assert params[0] == "202604"


def test_carriere_scope_for_explicit_export_mois_forces_interval() -> None:
    from sme_gend.config.liste_generale_scope import carriere_scope_for_explicit_export_mois

    sql, params = carriere_scope_for_explicit_export_mois("2026-05")
    assert sql == carriere_scope_sql("2026-05")[0]
    assert params == carriere_scope_sql("2026-05")[1]


def test_carriere_scope_for_explicit_export_mois_matches_operational_view() -> None:
    """Même mois que la session : export explicite = intervalle, pas repli est_actuelle."""
    from sme_gend.config.liste_generale_scope import carriere_scope_for_explicit_export_mois

    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx, patch(
        "sme_gend.config.sme_context.is_mois_ferme", return_value=False
    ):
        load_ctx.return_value.mois_actif = "2026-05"
        st = _settings(mysql_view_mois=None)
        assert resolve_carriere_scope_mois(st) == "2026-05"
        exp_sql, exp_params = carriere_scope_for_explicit_export_mois("2026-05")
        assert exp_params[0] == "202605"
        assert exp_sql == carriere_scope_sql("2026-05")[0]


def test_carriere_scope_for_numerique_uses_month_snapshot_on_active_session() -> None:
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx, patch(
        "sme_gend.config.sme_context.is_mois_ferme", return_value=False
    ):
        load_ctx.return_value.mois_actif = "2026-05"
        st = _settings(mysql_view_mois=None)
        assert resolve_carriere_scope_mois(st) == "2026-05"
        sql, params = carriere_scope_for_numerique(st)
        assert "date_debut" in sql
        assert params[0] == "202605"


def test_settings_for_numerique_data_pins_view_mois_on_active_session() -> None:
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-05"
        st = _settings(mysql_view_mois=None)
        out = settings_for_numerique_data(st)
        assert out.mysql_view_mois == "2026-05"
