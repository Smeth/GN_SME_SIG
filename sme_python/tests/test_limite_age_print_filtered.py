"""Impression des listes filtrées du module Limite d'âge."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.limite_age_service import LimiteAgeService, LimiteDateResult
from sme_gend.services.retraite_liste import RetraiteLigne
from sme_gend.ui.views import limite_age_view as view


@pytest.fixture(autouse=True)
def authorized_operator(monkeypatch):
    monkeypatch.setattr(view, "can_export", lambda: True)
    monkeypatch.setattr(view, "require_export", lambda parent: True)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _person(name: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=name,
        noms=name,
        date_naissance=date(1980, 1, 1),
        lieu_naissance=None,
        grade="GND",
        date_grade=None,
        date_service=date(2000, 1, 1),
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_attestation_print_button_uses_visible_filtered_rows(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []
    monkeypatch.setattr(
        view,
        "_print_filtered_personnel_table",
        lambda *args: captured.append(args),
    )
    tab = view._AttestationsTab(Settings(database_path=Path(".")), LimiteAgeService())
    result = LimiteDateResult(date(2040, 1, 1), "âge", date(2040, 1, 1), None, None)
    tab._all_rows = [
        (_person("Agent filtré"), result),
        (_person("Agent non filtré"), result),
    ]
    tab._fresh = True
    tab._apply_filter()
    tab._search.setText("Agent filtré")

    tab._btn_print_filtered.click()

    assert captured[0][1] == "Liste des personnes — limite d'âge"
    assert captured[0][2] == tuple(view._HEADERS[1:])
    assert captured[0][3][0][1] == "AGENT Filtré"
    assert len(captured[0][3]) == 1
    tab.close()


def test_retirement_print_button_uses_visible_filtered_rows(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []
    monkeypatch.setattr(
        view,
        "_print_filtered_personnel_table",
        lambda *args: captured.append(args),
    )
    tab = view._RetraiteTab(Settings(database_path=Path(".")), LimiteAgeService())
    person = _person("Agent retraite")
    tab._model.set_grade_codes({"GND": "GND"})
    tab._model.load([RetraiteLigne(person, "Limite d'âge", date(2030, 1, 1), 1200)])

    tab._btn_print_filtered.click()

    assert captured[0][1] == "Liste des échéances de retraite"
    assert captured[0][3][0][1] == "AGENT Retraite"
    assert len(captured[0][3]) == 1
    tab.close()
