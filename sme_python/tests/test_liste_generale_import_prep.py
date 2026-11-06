"""Tests préparation import liste générale."""

from __future__ import annotations

from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.liste_generale_row_prep import prepare_liste_generale_rows_for_import


def _row(
    *,
    row_id: str = "1",
    noms: str = "DUPONT Jean",
    mat: str | None = "12345",
) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id,
        noms=noms,
        date_naissance=None,
        lieu_naissance=None,
        grade="CNE",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=mat,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=1,
        presseance=1,
    )


def test_empty_id_is_auto_generated() -> None:
    rows = [_row(row_id="", noms="SANS ID", mat="2-98-42786")]
    out, empty, auto, dup = prepare_liste_generale_rows_for_import(rows, import_lot_id=42)
    assert len(out) == 1
    assert empty == 1
    assert auto == 1
    assert dup == 0
    assert out[0].id.startswith("GEN-")
    assert out[0].id.count("-") == 1  # GEN-{fp}, sans lot
    # Stable entre lots : même contenu → même clé
    out2, *_ = prepare_liste_generale_rows_for_import(rows, import_lot_id=99)
    assert out2[0].id == out[0].id


def test_empty_id_without_matricule_stable_across_row_order() -> None:
    a = _row(row_id="", noms="ANONYME Un", mat=None)
    b = _row(row_id="", noms="ANONYME Deux", mat=None)
    out1, *_ = prepare_liste_generale_rows_for_import([a, b], import_lot_id=1)
    out2, *_ = prepare_liste_generale_rows_for_import([b, a], import_lot_id=1)
    ids1 = {r.noms: r.id for r in out1}
    ids2 = {r.noms: r.id for r in out2}
    assert ids1 == ids2


def test_same_matricule_different_ids_stay_separate() -> None:
    rows = [
        _row(row_id="A1", mat="99999", noms="Personne A"),
        _row(row_id="A2", mat="99999", noms="Personne B"),
    ]
    out, empty, auto, dup = prepare_liste_generale_rows_for_import(rows, import_lot_id=1)
    assert empty == 0
    assert auto == 0
    assert dup == 0
    assert out[0].id == "A1"
    assert out[1].id == "A2"


def test_duplicate_access_id_gets_suffix() -> None:
    rows = [
        _row(row_id="X", noms="Un"),
        _row(row_id="Y", noms="Milieu"),
        _row(row_id="X", noms="Deux"),
    ]
    out, _, _, dup = prepare_liste_generale_rows_for_import(rows, import_lot_id=1)
    assert dup == 1
    assert out[0].id == "X"
    assert out[1].id == "Y"
    assert out[2].id == "X#1"  # rang d'occurrence, pas index fichier
