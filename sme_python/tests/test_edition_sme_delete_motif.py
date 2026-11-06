from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.views.edition_sme_view import _EditionModel


def _row(row_id: str = "1") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id,
        noms=f"Agent {row_id}",
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


class _Personnel:
    def __init__(self) -> None:
        self.deletions: list[tuple[ListeGeneraleRow, str]] = []

    def delete_personnel(self, row: ListeGeneraleRow, *, motif: str) -> None:
        self.deletions.append((row, motif))


def test_remove_rows_requires_and_passes_perte_motif() -> None:
    row = _row()
    personnel = _Personnel()
    model = _EditionModel()
    model.load([row], None, [], [], [], [], personnel=personnel)

    assert model.remove_rows([0], motif="  ") == (
        0,
        ["Suppression impossible — le motif de perte est obligatoire."],
    )
    assert personnel.deletions == []
    assert model._rows == [row]

    assert model.remove_rows([0], motif="RETRAITE") == (1, [])
    assert personnel.deletions == [(row, "RETRAITE")]
    assert model._rows == []


def test_remove_rows_never_falls_back_to_untracked_repository_delete() -> None:
    row = _row()
    model = _EditionModel()
    model.load([row], object(), [], [], [], [])

    deleted, errors = model.remove_rows([0], motif="RETRAITE")

    assert deleted == 0
    assert "service personnel" in errors[0]
    assert model._rows == [row]
