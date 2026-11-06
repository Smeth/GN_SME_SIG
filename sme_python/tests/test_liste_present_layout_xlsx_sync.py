"""Tests synchronisation layout depuis maquette Excel."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.liste_present_layout_xlsx_sync import parse_presents_xlsx_sections


def test_parse_presents_xlsx_sections_dpf_order():
    xlsx = Path(__file__).resolve().parents[1] / "liste_des_presents_mai.xlsx"
    if not xlsx.is_file():
        return
    sections = parse_presents_xlsx_sections(xlsx)
    dpf = next(
        (subs for title, subs in sections if "PERSONNEL" in title and "FORMATION" in title),
        None,
    )
    assert dpf is not None
    assert dpf[:3] == [
        "SECRETARIAT",
        "DIVISION GESTION DU PERSONNEL",
        "DIVISION RECRUTEMENT ET FORMATION",
    ]
