"""Règles périmètre / écarts liste détachés (Padmin vs structure)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import detaches_service as ds


def _row(
    *,
    pid: str = "1",
    padmin: str | None = None,
    structure: str | None = None,
    sub_arme: str | None = "ABC",
    ndecision: str | None = None,
) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=pid,
        noms="Test",
        date_naissance=datetime(1990, 1, 1),
        lieu_naissance=None,
        grade="Sdt",
        date_grade=None,
        date_service=None,
        ndecision=ndecision,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=padmin,
        fonction=None,
        subdivision_arme=sub_arme,
        diplome=None,
        date_obtention=None,
        structure=structure,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_padmin_detache_in_scope() -> None:
    r = _row(padmin="détaché", structure="AUTRE")
    assert ds.is_padmin_detache_row(r)
    assert ds.in_detaches_scope(r)
    assert ds.detache_ecart_remarque(r) == ""


def test_structure_detache_without_padmin_flagged() -> None:
    r = _row(padmin="Mission", structure="DETACHE")
    assert not ds.is_padmin_detache_row(r)
    assert ds.is_structure_detache_row(r)
    assert ds.in_detaches_scope(r)
    assert "Mission" in ds.detache_ecart_remarque(r)
    assert "À vérifier" in ds.detache_ecart_remarque(r)


def test_structure_code_detache_only() -> None:
    assert not ds.is_structure_detache_row(_row(padmin="Malade", structure="Détaché"))
    assert ds.is_structure_detache_row(_row(padmin="Malade", structure="DETACHE"))
    assert ds.is_structure_detache_row(_row(padmin="Malade", structure="detache"))


def test_filter_detaches_sans_ref_note() -> None:
    a = _row(pid="1", padmin="Détaché", ndecision=None)
    b = _row(pid="2", padmin="Détaché", ndecision="NS/2026-01")
    c = _row(pid="3", padmin="Détaché", ndecision="   ")
    rows = [a, b, c]
    assert ds.filter_detaches_rows(rows, sans_note_only=True) == [a, c]
    assert ds.filter_detaches_rows(rows, avec_note_only=True) == [b]
    assert ds.filter_detaches_rows(rows) == rows


def test_filter_detaches_display_search_and_sigle() -> None:
    a = replace(_row(pid="1", padmin="Détaché", sub_arme="GR"), noms="DUPONT Jean")
    b = replace(_row(pid="2", padmin="Détaché", sub_arme="DGSP"), noms="MARTIN Paul")
    rows = [a, b]
    assert ds.filter_detaches_display(rows, sigle="GR") == [a]
    assert ds.filter_detaches_display(rows, search="martin") == [b]


def test_filter_detaches_display_structure_and_padmin() -> None:
    a = _row(pid="1", padmin="Détaché", structure="DETACHE")
    b = _row(pid="2", padmin="Mission", structure="GGF")
    c = _row(pid="3", padmin="Présent", structure="DETACHE")
    rows = [a, b, c]
    assert ds.filter_detaches_display(rows, structure="DETACHE") == [a, c]
    assert ds.filter_detaches_display(rows, padmin="Mission") == [b]


def test_designation_bloc_excel() -> None:
    from sme_gend.models.detaches_file_config import DetachesFileConfig, DetachesStructureEntry

    cfg = DetachesFileConfig(
        structures=[
            DetachesStructureEntry(sigle="AN", ordre=1, titres_lignes=["Titre long", "Sous-titre"]),
        ]
    )
    assert ds.designation_bloc_excel(cfg, "an") == "Titre long — Sous-titre"
    assert ds.designation_bloc_excel(cfg, "INCONNU") == ""


def test_archive_slug_candidates_mars_2026() -> None:
    from sme_gend.services.sme_month_closure import archive_slug_candidates_for_month

    s = archive_slug_candidates_for_month("2026-03")
    assert "2026-03" in s
    assert "202603" in s


def test_shift_sme_month_yyyy_mm() -> None:
    from sme_gend.services.sme_month_closure import shift_sme_month_yyyy_mm

    assert shift_sme_month_yyyy_mm("2026-05", delta_months=-1) == "2026-04"
    assert shift_sme_month_yyyy_mm("2026-01", delta_months=-1) == "2025-12"
    assert shift_sme_month_yyyy_mm("2026-12", delta_months=1) == "2027-01"


def test_out_of_scope() -> None:
    r = _row(padmin="Malade", structure="REG")
    assert not ds.in_detaches_scope(r)
    assert ds.detache_ecart_remarque(r) == ""
