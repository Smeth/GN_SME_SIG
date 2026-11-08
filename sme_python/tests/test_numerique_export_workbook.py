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
    def forbidden(*a, **kw):
        pytest.fail("L'export direct ne doit exiger ni migration ni publication.")
    monkeypatch.setattr("sme_gend.services.schema_migrations.require_capability", forbidden)
    monkeypatch.setattr("sme_gend.services.sme_publication_registry.register_export",
                        forbidden)
    from sme_gend.services import numerique_excel_export as export
    from sme_gend.models.detaches_file_config import DetachesFileConfig
    tpl = Path(__file__).resolve().parents[2] / "SME_GEND_model.xlsx"
    seen_mois = []
    def capture(settings, mois, grade_order, movement_settings, *, include_revision):
        assert include_revision is False
        seen_mois.append((settings.mysql_view_mois, mois, movement_settings.mysql_view_mois))
        return {"mois": mois, "rows": [], "grades": [], "details": {
            key: {} for key in ("missions", "malades", "deserteurs", "stages_loc", "stages_ex",
                                "stages_internal", "conges", "conges_by_type", "positions")},
            "detaches": [], "previous_counts": [None] * 10, "movements": [],
            "detaches_config": DetachesFileConfig().to_dict()}, {"captured_at_utc": "2026-11-06T10:00:00+00:00"}
    monkeypatch.setattr(export, "_capture_numerique_workbook_data", capture)
    monkeypatch.setattr(export, "_capture_publication_inputs", forbidden)
    monkeypatch.setattr("sme_gend.services.sme_export_revision.revision_root", lambda settings: tmp_path / "revisions")
    logs = []
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", lambda **kw: logs.append(kw))
    dest = tmp_path / "out.xlsx"
    result = export_numerique_workbook(_settings(), tpl, dest, export_mois="2025-06", mois_actif="2026-04")
    assert seen_mois == [("2025-06", "2025-06", "2025-06")]
    assert result.path == dest and result.month == "2025-06"
    assert not (tmp_path / "revisions").exists()
    assert logs[0]["meta"]["export_mode"] == "direct"
    assert logs[0]["mois"] == "2025-06"
    wb = load_workbook(dest)
    original = load_workbook(tpl)
    try:
        assert wb.sheetnames == original.sheetnames
        assert wb["NUMERIQUE"]["B8"].data_type == "f"
        assert set(map(str, wb["NUMERIQUE"].merged_cells.ranges)) == set(map(str, original["NUMERIQUE"].merged_cells.ranges))
        assert "2025" in str(wb["NUMERIQUE"]["A3"].value)
        assert "2025" in str(wb["GAIN PERTE"].cell(3, 1).value)
    finally:
        wb.close()
        original.close()


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
