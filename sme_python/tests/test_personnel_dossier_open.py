"""Tests module d'ouverture robuste de la fiche dossier."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.structure import StructureRow
from sme_gend.ui.personnel_dossier_open import resolve_dossier_row


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeReferentiel:
    def grades(self):
        return []

    def structures(self):
        return [StructureRow(code="COMGEND", designation="Commandement", ordre_tri=1)]


def _row() -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="7",
        noms="MARTIN Paul",
        date_naissance=None,
        lieu_naissance=None,
        sexe="M",
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_resolve_dossier_row_prefers_fresh():
    svc = MagicMock()
    listed = _row()
    fresh = replace(listed, fonction="Chef")
    svc.get_by_id.return_value = fresh
    assert resolve_dossier_row(svc, listed) is fresh


def test_resolve_dossier_row_refuses_stale_situation_when_db_misses():
    svc = MagicMock()
    listed = _row()
    svc.get_by_id.return_value = None
    with pytest.raises(LookupError):
        resolve_dossier_row(svc, listed)


def test_resolve_dossier_row_skips_db_when_disabled():
    svc = MagicMock()
    listed = _row()
    assert resolve_dossier_row(svc, listed, refresh_from_db=False) is listed
    svc.get_by_id.assert_not_called()


def test_refresh_header_and_media_uses_ref_not_referentiel(qapp, monkeypatch) -> None:
    """Régression : _refresh_header_and_media doit appeler self._ref, pas self._referentiel."""
    from PySide6.QtWidgets import QLabel, QStyle, QStyleOption

    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog

    row = replace(_row(), grade="GD8", structure="COMGEND", noms="AKOUANGUE Gervais")
    ref = MagicMock()
    ref.grades.return_value = []
    ref.structures.return_value = []
    dlg = PersonnelDossierDialog.__new__(PersonnelDossierDialog)
    dlg._row = row
    dlg._ref = ref
    from pathlib import Path
    from sme_gend.config.settings import Settings
    dlg._settings = Settings(database_path=Path("unused"), mysql_view_mois="2026-09")
    monkeypatch.setattr("sme_gend.services.agent_identity_alias.identity_photo_path", lambda *_: None)
    dlg._title = QLabel()
    dlg._subtitle = QLabel()
    dlg._photo = QLabel()
    dlg._grade_pix = QLabel()
    dlg._media_hint = QLabel()
    dlg.style = MagicMock(return_value=MagicMock(spec=QStyle))
    dlg.style.return_value.standardIcon.return_value.pixmap.return_value = MagicMock()
    dlg._media_person_key = MagicMock(return_value="42")
    dlg._set_image_label = MagicMock()

    dlg._refresh_header_and_media()

    ref.grades.assert_called_once()
    assert "AKOUANGUE" in dlg._title.text()
