from __future__ import annotations

from datetime import date
from importlib import import_module
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.liste_generale_scope import ExportMoisContext
from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.excel_style_helpers import build_grade_abbrev_map
from sme_gend.services.liste_generale_cache import (
    get_liste_rows,
    invalidate_liste_cache,
)
from sme_gend.services.numerique_excel_export import satellite_grade_count_pairs
from sme_gend.services.numerique_preview_service import NumeriquePreviewService


def _settings() -> Settings:
    return Settings(database_path=Path("."), mysql_view_mois="2026-09")


def _person(grade: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="legacy-1",
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade=grade,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Déserteur",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_satellite_preview_grade_totals_use_template_grade_codes() -> None:
    grade_map = build_grade_abbrev_map(
        [
            GradeRefRow(
                code="GDB",
                libelle="GENERAL DE BRIGADE",
                pref_ord=1,
                effectif_g=None,
            )
        ]
    )
    totals = satellite_grade_count_pairs(
        [("GDB", 6)],
        [_person("GENERAL DE BRIGADE"), _person("GDB")],
        grade_abbrev_map=grade_map,
    )
    assert totals == [("GDB", 2)]


def test_invalidate_liste_cache_forces_a_fresh_read() -> None:
    settings = _settings()
    calls = []

    first = get_liste_rows(settings, lambda: calls.append("old") or ["old"])
    invalidate_liste_cache(settings)
    second = get_liste_rows(settings, lambda: calls.append("fresh") or ["fresh"])

    assert first == ["old"]
    assert second == ["fresh"]
    assert calls == ["old", "fresh"]


def test_combined_refresh_invalidates_cache_and_uses_one_silent_worker(
    monkeypatch,
) -> None:
    from sme_gend.services import liste_generale_cache
    view_module = import_module("sme_gend.ui.views.numerique_combined_view")
    from sme_gend.ui.views.numerique_combined_view import NumeriqueCombinedView

    app = QApplication.instance() or QApplication([])
    settings = _settings()

    class Preview:
        liste_settings = settings

        def __init__(self) -> None:
            self.calls = []
            self.fail_indispo = False

        def build_html_matrice(self):
            self.calls.append("matrice")
            return "<p>matrice</p>"

        def build_html_indispo(self):
            self.calls.append("indispo")
            if self.fail_indispo:
                raise RuntimeError("database unavailable")
            return "<p>indispo</p>"

        def build_html_satellite_etats(self):
            self.calls.append("satellite")
            return "<p>satellite</p>"

    preview = Preview()
    invalidated = []
    progress_calls = []
    monkeypatch.setattr(
        liste_generale_cache,
        "invalidate_liste_cache",
        lambda value: invalidated.append(value),
    )
    monkeypatch.setattr(
        view_module,
        "build_export_mois_context",
        lambda _settings: ExportMoisContext(
            export_mois="2026-09", mois_actif="2026-09", is_archive_view=False
        ),
    )

    def run_inline(_parent, _title, _text, fn, **kwargs):
        progress_calls.append(kwargs)
        return fn()

    monkeypatch.setattr(view_module, "run_with_progress", run_inline)
    view = NumeriqueCombinedView(settings, preview)
    view._refresh_all_previews(silent=True)
    assert invalidated == [settings]
    assert preview.calls == ["matrice", "indispo", "satellite"]
    assert progress_calls == [{"silent": True}]
    assert view._loaded

    preview.fail_indispo = True
    view._refresh_all_previews(silent=True)
    assert view._loaded
    assert "Impossible de charger cet aperçu" in view._browser_i.toHtml()

    # Refreshing a single tab must also bypass cached rows from another workstation.
    invalidated.clear()
    view._refresh_matrice(refresh_cache=True)
    assert invalidated == [settings]
    assert progress_calls[-1] == {"silent": False}
    view.close()
    app.processEvents()


def test_gains_pertes_read_failure_is_visible_in_preview(monkeypatch) -> None:
    import sme_gend.repositories.gain_perte_repository as repository

    class FailingRepository:
        def __init__(self, _settings) -> None:
            pass

        def fetch_for_month(self, _month):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(repository, "GainPerteRepository", FailingRepository)
    service = object.__new__(NumeriquePreviewService)
    service._gain_perte_settings = _settings()
    service._export_mois_context = lambda: ExportMoisContext(
        export_mois="2026-09", mois_actif="2026-09", is_archive_view=False
    )

    section = service._build_html_gain_perte_section()

    assert "Lecture des Gains &amp; Pertes impossible" in section


def test_baseline_period_lookup_errors_are_not_treated_as_missing_period(
    monkeypatch,
) -> None:
    import sme_gend.services.numerique_recap_baseline as baseline

    class FailingRepository:
        def __init__(self, _settings) -> None:
            pass

        def table_exists(self):
            return True

        def fetch_all(self):
            raise RuntimeError("period read failed")

    monkeypatch.setattr(baseline, "SmeRepository", FailingRepository)

    with pytest.raises(RuntimeError, match="period read failed"):
        baseline._sme_period_exists(_settings(), "2026-08")


class _Cursor:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, cursor) -> None:
        self.cursor_value = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_value


def test_stage_query_orders_duplicate_details_by_latest_date_then_id(
    monkeypatch,
) -> None:
    from sme_gend.repositories.mysql_numerique_repository import (
        MySQLNumeriqueRepository,
    )

    cursor = _Cursor(
        [
            ("legacy-1", "Latest", "Lieu B", None, date(2026, 8, 1), None, "legacy-1", "legacy-1"),
            ("legacy-1", "Older", "Lieu A", None, date(2026, 7, 1), None, "legacy-1", "legacy-1"),
        ]
    )
    repo = object.__new__(MySQLNumeriqueRepository)
    repo._settings = _settings()
    def read_connection(*, read_only=False):
        assert read_only
        return _Connection(cursor)
    monkeypatch.setattr(repo, "_connect", read_connection)

    details = repo._load_stages("LOCAL")

    assert "ORDER BY COALESCE(ps.date_debut, '1900-01-01') DESC, ps.id DESC" in cursor.query
    assert details["legacy-1"].nature_stage == "Latest"
