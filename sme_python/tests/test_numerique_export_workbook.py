"""Tests export classeur situation numérique — feuilles, mois, mise en page."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from openpyxl import load_workbook

from sme_gend.config.liste_generale_scope import settings_for_chosen_export_mois
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.numerique_excel_export import (
    SHEET_CONGE_ADMIN,
    _normalize_sheet_key,
    _workbook_sheet,
    export_numerique_workbook,
)


def _settings(**kw) -> Settings:
    from dataclasses import replace

    return replace(Settings(database_path=Path(".")), **kw)


def test_normalize_sheet_key_conge_admin_variants() -> None:
    assert _normalize_sheet_key("CONGé ADMIN") == _normalize_sheet_key("CONGÉ ADMIN")
    assert _normalize_sheet_key("CONGé ADMIN") == _normalize_sheet_key("conge admin")


def test_workbook_sheet_finds_conge_in_template() -> None:
    tpl = Path(__file__).resolve().parents[1] / "modeles/numerique.xlsx"
    if not tpl.is_file():
        pytest.skip("gabarit numerique.xlsx absent")
    wb = load_workbook(tpl, read_only=True)
    try:
        ws = _workbook_sheet(wb, SHEET_CONGE_ADMIN, "CONGÉ ADMIN")
        assert ws is not None
        assert "CONG" in ws.title.upper()
    finally:
        wb.close()


def test_settings_for_chosen_export_mois_overrides_view() -> None:
    base = _settings(mysql_view_mois=None)
    out = settings_for_chosen_export_mois(base, "2025-08")
    assert out.mysql_view_mois == "2025-08"


def test_export_numerique_workbook_uses_chosen_mois(monkeypatch, tmp_path: Path) -> None:
    tpl = Path(__file__).resolve().parents[2] / "SME_GEND_model.xlsx"
    if not tpl.is_file():
        pytest.skip("gabarit numerique.xlsx absent")

    seen_mois: list[str | None] = []

    def _fake_cached(settings: Settings) -> list[ListeGeneraleRow]:
        seen_mois.append(settings.mysql_view_mois)
        return []

    class _EmptyRepo:
        def __init__(self, _s: Settings) -> None:
            pass

        def fetch_all(self):
            return []

        def fetch_flat_hierarchy(self):
            return []

    class _EmptySat:
        def __init__(self, _s: Settings) -> None:
            pass

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

    class _EmptyDetaches:
        def __init__(self, _s: Settings) -> None:
            pass

        def fetch_detaches(self):
            return []

    class _EmptyDetachesCfg:
        def __init__(self, _s: Settings) -> None:
            pass

        def load(self):
            from sme_gend.models.detaches_file_config import DetachesFileConfig

            return DetachesFileConfig()

    monkeypatch.setattr(
        "sme_gend.services.numerique_excel_export.cached_numerique_liste_rows",
        _fake_cached,
    )
    monkeypatch.setattr(
        "sme_gend.services.numerique_excel_export.NumeriqueSatelliteRepository",
        _EmptySat,
    )
    monkeypatch.setattr(
        "sme_gend.services.numerique_excel_export.SubdivisionRepository",
        _EmptyRepo,
    )
    monkeypatch.setattr(
        "sme_gend.repositories.grade_repository.GradeRepository",
        _EmptyRepo,
    )
    monkeypatch.setattr(
        "sme_gend.services.detaches_service.DetachesService",
        _EmptyDetaches,
    )
    monkeypatch.setattr(
        "sme_gend.services.detaches_config_service.DetachesConfigService",
        _EmptyDetachesCfg,
    )
    monkeypatch.setattr(
        "sme_gend.services.numerique_recap_baseline.resolve_recap_cd_baseline",
        lambda *_a, **_k: [None] * 10,
    )
    monkeypatch.setattr(
        "sme_gend.services.numerique_excel_export.add_liste_present_sheet",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "sme_gend.services.doc_gen_log.log_doc_generation",
        lambda **_k: None,
    )
    monkeypatch.setattr("sme_gend.services.clean_sme_exports._movements", lambda *a, **kw: [])
    class _EmptyGainPerteRepo:
        def __init__(self, _s): pass
        def fetch_for_month(self, _m): return []
    monkeypatch.setattr("sme_gend.repositories.gain_perte_repository.GainPerteRepository", _EmptyGainPerteRepo)
    monkeypatch.setattr("sme_gend.services.gain_perte_coherence.load_unsignaled_pertes", lambda *a, **kw: [])

    dest = tmp_path / "out.xlsx"
    st = _settings(mysql_view_mois=None)
    with patch("sme_gend.config.liste_generale_scope.load_sme_context") as load_ctx:
        load_ctx.return_value.mois_actif = "2026-04"
        export_numerique_workbook(st, tpl, dest, export_mois="2025-06")

    assert seen_mois == ["2025-06"]
    assert dest.is_file()
    wb = load_workbook(dest, read_only=False, data_only=False)
    try:
        original = load_workbook(tpl)
        assert wb.sheetnames == original.sheetnames
        assert set(map(str, wb['NUMERIQUE'].merged_cells.ranges)) == set(map(str, original['NUMERIQUE'].merged_cells.ranges))
        assert wb['NUMERIQUE']['B8'].data_type == 'f'
        original.close()
        assert "LISTE GENERALE" in wb.sheetnames
        assert "NUMERIQUE" in wb.sheetnames
        ws_liste = wb["LISTE GENERALE"]
        assert ws_liste.page_setup.orientation == "landscape"
        ws_num = wb["NUMERIQUE"]
        title = ws_num["A3"].value
        assert title and "2025" in str(title)
        ws_gp = wb["GAIN PERTE"]
        assert "2025" in str(ws_gp.cell(row=3, column=1).value)
    finally:
        wb.close()


def test_numeric_export_stops_when_gains_pertes_cannot_be_read(monkeypatch):
    from openpyxl import Workbook
    from sme_gend.services.numerique_excel_export import fill_gains_pertes_sheet
    from sme_gend.config.settings import Settings
    from pathlib import Path
    class _BrokenRepo:
        def __init__(self, _s): pass
        def fetch_for_month(self, _m): raise RuntimeError("mysql offline")
    monkeypatch.setattr("sme_gend.repositories.gain_perte_repository.GainPerteRepository", _BrokenRepo)
    wb=Workbook();wb.active.title="GAIN PERTE"
    with pytest.raises(RuntimeError,match="lecture des gains et pertes impossible"):
        fill_gains_pertes_sheet(wb,Settings(database_path=Path("unused.mdb")),"2026-09")
    wb.close()
