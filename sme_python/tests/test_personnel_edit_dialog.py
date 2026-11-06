"""Tests dialogue modification personnel — ordre d'initialisation des widgets."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.structure import StructureRow
from sme_gend.ui.personnel_edit_dialog import PersonnelEditDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeReferentiel:
    def grades(self):
        return []

    def position_admin_choices(self):
        return ["Présent"]

    def structures(self):
        return [StructureRow(code="COMGEND", designation="Commandement", ordre_tri=1)]

    def subdivision_arme_choices_for_structure(self, structure_code: str) -> list[str]:
        del structure_code
        return ["Commandement Gendarmerie"]

    def fonction_choices_for_structure(self, structure_code: str) -> list[str]:
        del structure_code
        return ["COMGEND"]


def _sample_row() -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="123",
        noms="TEST Agent",
        date_naissance=None,
        lieu_naissance=None,
        sexe="M",
        grade="GDB",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction="COMGEND",
        subdivision_arme="Commandement Gendarmerie",
        diplome=None,
        date_obtention=None,
        structure="COMGEND",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_personnel_edit_dialog_constructs_without_structure_attribute_error(qapp) -> None:
    dlg = PersonnelEditDialog(None, _sample_row(), _FakeReferentiel())  # type: ignore[arg-type]
    assert dlg._structure is not None
    assert dlg._fonction.count() >= 1
    assert dlg._sub_arme.count() >= 1



def test_full_personnel_editor_preserves_the_access_join_key(qapp, monkeypatch):
    from dataclasses import replace
    row = replace(_sample_row(), access_join_id="SYNTHETIC-ACCESS-42")
    dlg = PersonnelEditDialog(None, row, _FakeReferentiel())
    # This contract concerns the hidden identity, with no catalog mutation.
    monkeypatch.setattr(dlg, "_structure_code", lambda:"")
    dlg._on_save()
    assert dlg._result is not None
    assert dlg.result_row().id == row.id
    assert dlg.result_row().access_join_id == row.access_join_id



@pytest.mark.parametrize("cancel_position", [False, True])
def test_full_form_prepares_but_never_writes_before_owner_transaction(qapp, monkeypatch, cancel_position):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QDialog
    from sme_gend.config.settings import Settings
    row = _sample_row()
    dlg = PersonnelEditDialog(None, row, _FakeReferentiel())
    dlg._settings = Settings(database_path=None)
    monkeypatch.setattr(dlg, "_structure_code", lambda:"")
    writer = MagicMock()
    planner = MagicMock(return_value=False if cancel_position else writer)
    monkeypatch.setattr("sme_gend.services.padmin_satellite_apply.prepare_position_change", planner)
    dlg._on_save()
    planner.assert_called_once()
    writer.assert_not_called()
    assert dlg.result() == (QDialog.DialogCode.Rejected if cancel_position else QDialog.DialogCode.Accepted)


def test_new_form_has_internal_identity_before_any_satellite_prompt(qapp, monkeypatch):
    from dataclasses import replace
    from sme_gend.services.safe_paths import file_key
    row = replace(_sample_row(), id="0", access_join_id=None)
    dlg = PersonnelEditDialog(None, row, _FakeReferentiel())
    key = dlg._original.satellite_join_key()
    assert key.startswith("GEN-APP-")
    assert file_key(key) == key
    monkeypatch.setattr(dlg, "_structure_code", lambda:"")
    dlg._on_save()
    assert dlg.result_row().satellite_join_key() == key
    assert row.access_join_id is None  # The caller's original object is preserved.
