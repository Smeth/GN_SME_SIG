"""Tests alignement mois archive : liste, cache, export numérique."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sme_gend.config.liste_generale_scope import (
    build_export_mois_context,
    resolve_export_mois,
    resolve_liste_view_mois,
)
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import liste_generale_cache as cache_mod
from sme_gend.services.numerique_export_summary import (
    build_numerique_export_summary,
    format_numerique_export_preview_text,
)
from sme_gend.services.numerique_excel_export import month_title_from_mois_actif


def _settings(**kw) -> Settings:
    return replace(Settings(database_path=Path(".")), **kw)


def test_liste_cache_key_differs_by_archive_mois() -> None:
    st_a = _settings(mysql_view_mois="2025-06")
    st_b = _settings(mysql_view_mois="2025-07")
    assert cache_mod.liste_cache_key(st_a) != cache_mod.liste_cache_key(st_b)


def test_cached_liste_rows_passes_archive_mois_to_repository(monkeypatch) -> None:
    archive_st = _settings(mysql_view_mois="2025-06")
    view_mois_seen: list[str | None] = []

    class _FakeRepo:
        def __init__(self, settings: Settings) -> None:
            self._settings = settings

        def fetch_all(self) -> list[ListeGeneraleRow]:
            view_mois_seen.append(resolve_liste_view_mois(self._settings))
            return []

    monkeypatch.setattr(
        "sme_gend.repositories.liste_generale_repository.ListeGeneraleRepository",
        _FakeRepo,
    )
    monkeypatch.setattr(
        "sme_gend.repositories.grade_repository.GradeRepository",
        lambda _s: type("G", (), {"fetch_all": lambda self: []})(),
    )

    cache_mod.invalidate_liste_cache()
    cache_mod.cached_liste_rows(archive_st)

    assert view_mois_seen == ["2025-06"]


def test_cached_numerique_liste_rows_aligned_with_liste_generale(monkeypatch) -> None:
    st = _settings(mysql_view_mois=None)
    fetch_all_calls = 0
    fetch_at_mois_calls = 0

    class _FakeRepo:
        def __init__(self, settings: Settings) -> None:
            self._settings = settings

        def fetch_all_at_mois(self, mois_ym: str) -> list[ListeGeneraleRow]:
            nonlocal fetch_at_mois_calls
            fetch_at_mois_calls += 1
            return []

        def fetch_all(self) -> list[ListeGeneraleRow]:
            nonlocal fetch_all_calls
            fetch_all_calls += 1
            return []

    monkeypatch.setattr(
        "sme_gend.repositories.liste_generale_repository.ListeGeneraleRepository",
        _FakeRepo,
    )
    monkeypatch.setattr(
        "sme_gend.repositories.grade_repository.GradeRepository",
        lambda _s: type("G", (), {"fetch_all": lambda self: []})(),
    )

    cache_mod.invalidate_liste_cache()
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-05"
        cache_mod.cached_numerique_liste_rows(st)
        cache_mod.cached_liste_rows(st)

    assert fetch_all_calls == 1
    assert fetch_at_mois_calls == 0


def test_numerique_satellite_repo_gets_archive_settings(monkeypatch) -> None:
    st = _settings(mysql_view_mois="2025-06")
    seen: list[str | None] = []

    class _FakeMySQLNumerique:
        def __init__(self, settings: Settings) -> None:
            seen.append(settings.mysql_view_mois)

        def load_missions(self):
            return {}

        def load_malades(self):
            return {}

        def load_deserteurs(self):
            return {}

        def load_stages_loc(self):
            return {}

        def load_stages_ex(self):
            return {}

    monkeypatch.setattr(
        "sme_gend.repositories.mysql_numerique_repository.MySQLNumeriqueRepository",
        _FakeMySQLNumerique,
    )
    from sme_gend.repositories.numerique_satellite_repository import (
        NumeriqueSatelliteRepository,
    )

    NumeriqueSatelliteRepository(st)
    assert seen == ["2025-06"]


def test_numerique_export_summary_archive_banner(monkeypatch) -> None:
    st = _settings(mysql_view_mois="2025-06")

    monkeypatch.setattr(
        "sme_gend.services.numerique_export_summary.cached_numerique_liste_rows",
        lambda _s: [
            ListeGeneraleRow(
                id="1",
                noms="A",
                date_naissance=None,
                lieu_naissance=None,
                grade="GND",
                date_grade=None,
                date_service=None,
                ndecision=None,
                matricule_militaire=None,
                matricule_solde=None,
                position_admin=None,
                fonction=None,
                subdivision_arme=None,
                diplome=None,
                date_obtention=None,
                structure="U001",
                obs=None,
                ordre_tri=None,
                presseance=None,
            )
        ],
    )

    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        summary = build_numerique_export_summary(st)

    assert summary.export_mois == "2025-06"
    assert summary.mois_actif == "2026-04"
    assert summary.is_archive_view is True
    assert summary.agent_count == 1

    preview = format_numerique_export_preview_text(summary)
    assert "2025-06" in preview
    assert "archive consultée" in preview.lower()


def test_numerique_export_summary_satellite_counts_use_padmin(monkeypatch) -> None:
    st = _settings(mysql_view_mois="2026-07")

    monkeypatch.setattr(
        "sme_gend.services.numerique_export_summary.cached_numerique_liste_rows",
        lambda _s: [
            ListeGeneraleRow(
                id="1",
                noms="Missionnaire",
                date_naissance=None,
                lieu_naissance=None,
                grade="GND",
                date_grade=None,
                date_service=None,
                ndecision=None,
                matricule_militaire=None,
                matricule_solde=None,
                position_admin="Mission",
                fonction=None,
                subdivision_arme=None,
                diplome=None,
                date_obtention=None,
                structure="U001",
                obs=None,
                ordre_tri=None,
                presseance=None,
            ),
            ListeGeneraleRow(
                id="2",
                noms="Present",
                date_naissance=None,
                lieu_naissance=None,
                grade="GND",
                date_grade=None,
                date_service=None,
                ndecision=None,
                matricule_militaire=None,
                matricule_solde=None,
                position_admin="Présent",
                fonction=None,
                subdivision_arme=None,
                diplome=None,
                date_obtention=None,
                structure="U001",
                obs=None,
                ordre_tri=None,
                presseance=None,
            ),
        ],
    )
    monkeypatch.setattr(
        "sme_gend.services.detaches_service.DetachesService",
        type(
            "_Det",
            (),
            {
                "__init__": lambda self, _s: None,
                "fetch_detaches": lambda self: [],
            },
        ),
    )

    summary = build_numerique_export_summary(st)
    assert summary.agent_count == 2
    assert summary.missions == 1
    assert summary.malades == 0


def test_export_mois_drives_numerique_month_title() -> None:
    st = _settings(mysql_view_mois="2025-06")
    assert resolve_export_mois(st) == "2025-06"
    title = month_title_from_mois_actif(resolve_export_mois(st) or "")
    assert "2025" in title
    assert "Juin" in title or "juin" in title.lower()


def test_build_export_context_active_month_not_archive() -> None:
    st = _settings(mysql_view_mois=None)
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        ctx = build_export_mois_context(st)
    assert ctx.export_mois == "2026-04"
    assert ctx.is_archive_view is False
