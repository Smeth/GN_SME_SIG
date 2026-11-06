"""Tests politique données sensibles."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.sensitive_data_policy import (
    SensitiveDataPermissionError,
    assert_may_edit_personnel_sensitive_fields,
    assert_may_mutate_dossier_history,
    session_may_edit_sensitive_data,
)


def _row(**kw) -> ListeGeneraleRow:
    base = ListeGeneraleRow(
        id="1",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="LTN",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire="123",
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="CAB",
        obs=None,
        ordre_tri=None,
        presseance=None,
        sexe="M",
    )
    return replace(base, **kw)


def test_operateur_may_change_matricule(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_personnel_identity",
        lambda: True,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_age_data",
        lambda: False,
    )
    before = _row()
    after = _row(matricule_militaire="999")
    assert_may_edit_personnel_sensitive_fields(before, after)


def test_operateur_blocked_on_matricule_without_edit_permission(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_personnel_identity",
        lambda: False,
    )
    before = _row()
    after = _row(matricule_militaire="999")
    with pytest.raises(SensitiveDataPermissionError, match="Matricule militaire"):
        assert_may_edit_personnel_sensitive_fields(before, after)


def test_operateur_may_change_structure(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_personnel_identity",
        lambda: True,
    )
    before = _row()
    after = _row(structure="EMGA")
    assert_may_edit_personnel_sensitive_fields(before, after)


def test_operateur_blocked_on_date_naissance(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_age_data",
        lambda: False,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_sensitive_data",
        lambda: True,
    )
    before = _row()
    after = _row(date_naissance=datetime(1990, 1, 1))
    with pytest.raises(SensitiveDataPermissionError, match="date de naissance"):
        assert_may_edit_personnel_sensitive_fields(before, after)


def test_admin_may_change_date_naissance_with_permission(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_age_data",
        lambda: True,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_sensitive_data",
        lambda: True,
    )
    before = _row()
    after = _row(date_naissance=datetime(1990, 1, 1))
    assert_may_edit_personnel_sensitive_fields(before, after)


def test_admin_may_change_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_sensitive_data",
        lambda: True,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_age_data",
        lambda: True,
    )
    before = _row()
    after = _row(noms="MARTIN Paul", date_naissance=datetime(1990, 1, 1))
    assert_may_edit_personnel_sensitive_fields(before, after)


def test_operateur_blocked_on_dossier_history(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.session_may_edit_sensitive_data",
        lambda: False,
    )
    with pytest.raises(SensitiveDataPermissionError):
        assert_may_mutate_dossier_history(action="ajouter une passation")
