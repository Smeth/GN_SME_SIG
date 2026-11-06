"""Tests éditeur de cellule Édition SME — perte de valeur & molette."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QComboBox

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.no_wheel_widgets import NoWheelComboBox
from sme_gend.ui.views.edition_sme_view import _COL_IDX, _EditionDelegate, _EditionModel

app = QApplication.instance() or QApplication([])


def _row(row_id: str = "1", *, grade: str | None = "MDL") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id, noms=f"Agent {row_id}", date_naissance=None, lieu_naissance=None,
        grade=grade, date_grade=None, date_service=None, ndecision=None,
        matricule_militaire=None, matricule_solde=None, position_admin="Présent",
        fonction=None, subdivision_arme=None, diplome=None, date_obtention=None,
        structure="U001", obs=None, ordre_tri=None, presseance=None,
    )


class _FakePersonnel:
    def __init__(self) -> None:
        self.updates: list[tuple[ListeGeneraleRow, ListeGeneraleRow]] = []

    def update_personnel(self, before: ListeGeneraleRow, after: ListeGeneraleRow) -> None:
        self.updates.append((before, after))


def _editor_for(model: _EditionModel, *, field: str):
    delegate = _EditionDelegate(model, None)
    idx = model.index(0, _COL_IDX[field])
    editor = delegate.createEditor(None, None, idx)
    return delegate, idx, editor


def test_grade_hors_referentiel_n_est_pas_efface() -> None:
    """Le combo doit conserver la valeur même absente des choix (regression)."""
    svc = _FakePersonnel()
    model = _EditionModel()
    # Valeur non présente dans le référentiel de grades
    model.load([_row(grade="GRADE-HORS-REF")], None, ["MDL"], [], [], [], personnel=svc)
    delegate, idx, editor = _editor_for(model, field="grade")

    delegate.setEditorData(editor, idx)
    assert isinstance(editor, QComboBox)
    assert editor.currentText() == "GRADE-HORS-REF"

    delegate.setModelData(editor, model, idx)
    # Avant correctif : currentText == "" → le grade était effacé en base.
    assert model._rows[0].grade == "GRADE-HORS-REF"
    assert svc.updates == []


def test_grade_present_dans_le_referentiel_est_selectionne() -> None:
    svc = _FakePersonnel()
    model = _EditionModel()
    model.load([_row(grade="MDL")], None, ["COL", "MDL"], [], [], [], personnel=svc)
    delegate, idx, editor = _editor_for(model, field="grade")

    delegate.setEditorData(editor, idx)
    assert editor.currentText() == "MDL"
    assert editor.currentIndex() >= 0

    delegate.setModelData(editor, model, idx)
    assert model._rows[0].grade == "MDL"
    assert svc.updates == []  # valeur identique : aucune écriture


def test_combo_editeur_ignore_la_molette() -> None:
    """Aucune modification de valeur par la molette sur un champ de sélection."""
    svc = _FakePersonnel()
    model = _EditionModel()
    model.load([_row(grade="MDL")], None, ["COL", "MDL"], [], [], [], personnel=svc)
    delegate, idx, editor = _editor_for(model, field="grade")
    delegate.setEditorData(editor, idx)

    assert isinstance(editor, NoWheelComboBox)

    class _Wheel:
        def __init__(self) -> None:
            self.ignored = False

        def ignore(self) -> None:
            self.ignored = True

        def accept(self) -> None:
            self.ignored = False

    ev = _Wheel()
    before = editor.currentIndex()
    editor.wheelEvent(ev)  # noqa: protected-access
    assert ev.ignored is True
    assert editor.currentIndex() == before


def _sub_row(row_id: str, *, structure: str, subdivision: str | None) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id, noms=f"Agent {row_id}", date_naissance=None, lieu_naissance=None,
        grade="MDL", date_grade=None, date_service=None, ndecision=None,
        matricule_militaire=None, matricule_solde=None, position_admin="Présent",
        fonction=None, subdivision_arme=subdivision, diplome=None, date_obtention=None,
        structure=structure, obs=None, ordre_tri=None, presseance=None,
    )


def test_subdivision_editeur_limite_aux_donnees_du_mois() -> None:
    """Éditeur : ne propose que les subdivisions présentes dans le mois affiché.

    ref_subdivision_arme conserve l'héritage des anciens SME (200+ libellés
    dont une seule fraction est utilisée ce mois-ci) : sans ce filtre, la liste
    mélangeait subdivisions actuelles et historiques (confusion utilisateur).
    """
    svc = _FakePersonnel()
    model = _EditionModel()
    model.load(
        [
            _sub_row("1", structure="U001", subdivision="ESC 1"),
            _sub_row("2", structure="U001", subdivision="ESC 2"),
            _sub_row("3", structure="U002", subdivision="BRIGADE A"),
        ],
        None, [], [], ["SUB HISTORIQUE ANCIEN SME"], [], personnel=svc,
    )
    delegate, idx, editor = _editor_for(model, field="subdivision_arme")

    texts = [editor.itemText(i) for i in range(editor.count())]
    assert "ESC 1" in texts
    assert "ESC 2" in texts
    assert "BRIGADE A" not in texts          # autre structure
    assert "SUB HISTORIQUE ANCIEN SME" not in texts  # repli non utilisé


def test_subdivision_editeur_conserve_la_valeur_courante() -> None:
    """La valeur de la ligne reste sélectionnable même hors des choix data."""
    svc = _FakePersonnel()
    model = _EditionModel()
    model.load(
        [
            _sub_row("1", structure="U001", subdivision="ESC 1"),
            _sub_row("2", structure="U001", subdivision="ANCIENNE SUB DISPAREE"),
        ],
        None, [], [], [], [], personnel=svc,
    )
    delegate = _EditionDelegate(model, None)
    idx = model.index(1, _COL_IDX["subdivision_arme"])
    options = delegate._subdivision_options_for_row(1)  # noqa: protected-access

    assert "ANCIENNE SUB DISPAREE" in options
    assert "ESC 1" in options


def test_subdivision_editeur_repli_referentiel_si_structure_sans_donnee() -> None:
    """Structure sans aucune subdivision ce mois : repli sur le référentiel."""
    svc = _FakePersonnel()
    model = _EditionModel()
    model.load(
        [
            _sub_row("1", structure="U001", subdivision=None),
            _sub_row("2", structure="U001", subdivision=None),
        ],
        None, [], [], [], [], personnel=svc,
    )
    model._settings = None  # référentiel indisponible → repli liste globale
    delegate = _EditionDelegate(model, None)
    options = delegate._subdivision_options_for_row(0)  # noqa: protected-access

    assert options == list(model.subarmes)


def test_subdivision_choices_par_structure() -> None:
    model = _EditionModel()
    model.load(
        [
            _sub_row("1", structure="U001", subdivision="ESC 2"),
            _sub_row("2", structure="U001", subdivision="ESC 1"),
            _sub_row("3", structure="U001", subdivision="ESC 1"),  # doublon
            _sub_row("4", structure="U002", subdivision="BRIGADE A"),
        ],
        None, [], [], [], [], personnel=svc_empty(),
    )
    assert model.subdivision_choices("U001") == ["ESC 1", "ESC 2"]
    assert model.subdivision_choices("U002") == ["BRIGADE A"]
    assert model.subdivision_choices(None) == [
        "BRIGADE A", "ESC 1", "ESC 2",
    ]
    assert model.subdivision_choices("U999") == []


def svc_empty() -> _FakePersonnel:
    return _FakePersonnel()
