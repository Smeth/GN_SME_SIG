"""Politique d’édition SME (statuts TabSme)."""

from __future__ import annotations

from sme_gend.services.sme_editing_policy import (
    STATUT_REOUVERT,
    is_statut_editable,
    is_statut_ferme,
)


def test_is_statut_ferme() -> None:
    assert is_statut_ferme("Fermé")
    assert is_statut_ferme("ferme")
    assert is_statut_ferme("Clos")
    assert not is_statut_ferme("Ouvert")
    assert not is_statut_ferme(STATUT_REOUVERT)
    assert not is_statut_ferme("")


def test_is_statut_editable() -> None:
    assert is_statut_editable("Ouvert")
    assert is_statut_editable(STATUT_REOUVERT)
    assert not is_statut_editable("Fermé")
