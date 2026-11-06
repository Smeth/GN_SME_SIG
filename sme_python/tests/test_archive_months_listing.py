"""Mois détectés depuis les archives disque (import ② sans MySQL)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.sme_month_closure import (
    extract_mois_from_archive_filename,
    list_months_from_archives,
)


def test_extract_mois_from_archive_filename_yyyy_mm_slug(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sme_month_closure.resolved_sme_archives_directory",
        lambda: tmp_path,
    )
    name = "sme_bundle_2026-04_20260424T120000Z.mdb"
    assert extract_mois_from_archive_filename(name) == "2026-04"


def test_extract_mois_from_archive_filename_compact_slug() -> None:
    name = "sme_bundle_202604_20260424T120000Z.mdb"
    assert extract_mois_from_archive_filename(name) == "2026-04"


def test_list_months_from_archives_scans_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sme_month_closure.resolved_sme_archives_directory",
        lambda: tmp_path,
    )
    (tmp_path / "sme_bundle_2026-05_20260501T100000Z.mdb").write_bytes(b"x")
    (tmp_path / "liste_present_layout_2026-05.json").write_text("{}")
    assert list_months_from_archives() == ["2026-05"]


def test_list_months_from_archives_ignores_non_bundle_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sme_month_closure.resolved_sme_archives_directory",
        lambda: tmp_path,
    )
    (tmp_path / "random_backup.mdb").write_bytes(b"x")
    assert list_months_from_archives() == []
