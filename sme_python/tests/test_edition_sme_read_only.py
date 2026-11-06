"""Tests verrouillage lecture seule — modèle Edition SME."""

from __future__ import annotations

from PySide6.QtCore import Qt

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.views.edition_sme_view import _EditionModel, _EDITABLE


def _sample_row() -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms="DUPONT",
        date_naissance=None,
        lieu_naissance=None,
        grade="MDL",
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
    )


def test_read_only_disables_editable_flags() -> None:
    model = _EditionModel()
    model.load([_sample_row()], None, [], [], [], [])
    idx = model.index(0, 1)
    assert model.flags(idx) & Qt.ItemFlag.ItemIsEditable

    model.set_read_only(True)
    assert not (model.flags(idx) & Qt.ItemFlag.ItemIsEditable)


def test_read_only_blocks_set_data() -> None:
    model = _EditionModel()
    model.load([_sample_row()], None, [], [], [], [])
    model.set_read_only(True)
    col = next(iter(_EDITABLE))
    idx = model.index(0, col)
    assert not model.setData(idx, "NOUVEAU", Qt.ItemDataRole.EditRole)


def test_read_only_blocks_bulk_update() -> None:
    model = _EditionModel()
    model.load([_sample_row()], None, [], [], [], [])
    model.set_read_only(True)
    updated, unchanged, errors = model.bulk_set_field([0], "structure", "U002")
    assert updated == 0
    assert errors
