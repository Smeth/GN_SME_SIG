"""Tests médias fiches personnel."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.personnel_media import grade_slug, save_grade_insignia, save_person_photo


def test_grade_slug() -> None:
    assert grade_slug("Général de Division") == "général_de_division"


def test_save_person_photo(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.personnel_media.resolved_personnel_media_root",
        lambda: tmp_path,
    )
    src = tmp_path / "src.png"
    src.write_bytes(b"fake")
    dest = save_person_photo("42", src)
    assert dest.is_file()
    assert dest.name == "42.png"


def test_grade_insignia_resolves_code_via_referentiel(tmp_path, monkeypatch) -> None:
    from sme_gend.models.grade_ref import GradeRefRow
    from sme_gend.services.personnel_media import grade_insignia_path

    grades_dir = tmp_path / "grades"
    grades_dir.mkdir(parents=True)
    (grades_dir / "lieutenant.png").write_bytes(b"fake")

    monkeypatch.setattr(
        "sme_gend.services.personnel_media._media_search_roots",
        lambda: [tmp_path],
    )
    grades = [GradeRefRow(code="LTN", libelle="Lieutenant", pref_ord=1, effectif_g=None)]
    path = grade_insignia_path("LTN", grades)
    assert path is not None
    assert path.name == "lieutenant.png"


def test_save_grade_insignia(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.personnel_media.resolved_personnel_media_root",
        lambda: tmp_path,
    )
    src = tmp_path / "ins.png"
    src.write_bytes(b"fake")
    dest = save_grade_insignia("MDL", src)
    assert dest.is_file()
    assert dest.stem == grade_slug("MDL")
