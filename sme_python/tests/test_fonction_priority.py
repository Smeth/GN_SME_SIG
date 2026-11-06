"""Tests ordre prioritaire des fonctions (ref_fonction + config MySQL)."""

from __future__ import annotations

from sme_gend.models.fonction_priority_config import FonctionPriorityConfig
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.fonction_priority_service import (
    LEGACY_DEFAULT_LABELS,
    bootstrap_default_config,
    build_priority_index,
)
from sme_gend.services.personnel_list_order import (
    sort_liste_generale_by_grade,
    sort_liste_generale_default_order,
    sort_liste_rows_like_ui,
)


def _row(nom: str, fonction: str | None, grade: str = "GND") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=nom,
        noms=nom,
        fonction=fonction,
        grade=grade,
        date_naissance=None,
        lieu_naissance=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def _grades() -> list[GradeRefRow]:
    return [
        GradeRefRow(code="COL", libelle="Colonel", pref_ord=1, effectif_g=None),
        GradeRefRow(code="GND", libelle="Gendarme", pref_ord=18, effectif_g=None),
    ]


def test_liste_generale_suit_lordre_des_grades():
    rows = [
        _row("Zulu", "COMGEND", "GND"),
        _row("Alpha", "AUTRE", "COL"),
        _row("Bravo", "COMGEND", "COL"),
    ]
    out = sort_liste_generale_by_grade(rows, _grades())
    assert [r.noms for r in out] == ["Alpha", "Bravo", "Zulu"]
    printed = sort_liste_rows_like_ui(rows, _grades(), None)
    assert [r.noms for r in printed] == ["Alpha", "Bravo", "Zulu"]


def test_grade_sort_uses_smallest_pref_ord_for_normalized_duplicate_labels():
    grades = [
        GradeRefRow(
            code="GEX-1",
            libelle="GRADE ECHELON SPECIAL",
            pref_ord=7,
            effectif_g=None,
        ),
        GradeRefRow(
            code="GEX-2",
            libelle="GRADE ECHELON SPECIAL",
            pref_ord=2,
            effectif_g=None,
        ),
        GradeRefRow(code="COL", libelle="Colonel", pref_ord=10, effectif_g=None),
    ]
    rows = [
        _row("Colonel", None, "COL"),
        _row("Special", None, "Grade—Échelon spécial"),
    ]

    assert [row.noms for row in sort_liste_generale_by_grade(rows, grades)] == [
        "Special",
        "Colonel",
    ]


def test_sort_with_configurable_priority_index():
    prio = {
        "comgend": 0,
        "directeur dpf": 1,
    }
    rows = [
        _row("B", "DIRECTEUR DPF"),
        _row("A", "COMGEND"),
        _row("C", "AUTRE FONCTION"),
    ]
    out = sort_liste_generale_default_order(rows, _grades(), priority_index=prio)
    assert [r.noms for r in out] == ["A", "B", "C"]


def test_bootstrap_resolves_legacy_labels(monkeypatch):
    class FakeRepo:
        def fetch_all(self):
            return [
                FonctionRefRow(id=10, libelle="COMGEND"),
                FonctionRefRow(id=20, libelle="C2 CEMGEND"),
            ]

    monkeypatch.setattr(
        "sme_gend.services.fonction_priority_service.FonctionsRepository",
        lambda _s: FakeRepo(),
    )
    cfg = bootstrap_default_config(None)  # type: ignore[arg-type]
    assert cfg.fonction_ids[:2] == [10, 20]


def test_build_priority_index_from_config(monkeypatch):
    class FakeRepo:
        def fetch_all(self):
            return [
                FonctionRefRow(id=1, libelle="COMGEND"),
                FonctionRefRow(id=2, libelle="DIRECTEUR DPF"),
            ]

    monkeypatch.setattr(
        "sme_gend.services.fonction_priority_service.ensure_fonction_priority_config",
        lambda _s: FonctionPriorityConfig(fonction_ids=[1, 2]),
    )
    monkeypatch.setattr(
        "sme_gend.services.fonction_priority_service.FonctionsRepository",
        lambda _s: FakeRepo(),
    )
    idx = build_priority_index(None)  # type: ignore[arg-type]
    assert idx["comgend"] == 0
    assert idx["directeur dpf"] == 1


def test_legacy_default_labels_count():
    assert len(LEGACY_DEFAULT_LABELS) == 10
