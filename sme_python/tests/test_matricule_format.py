"""Matricule solde — placeholder « / » = absent."""

from __future__ import annotations

from sme_gend.services.matricule_format import (
    format_matricule_solde_display,
    matricule_solde_is_empty,
    normalize_matricule_solde,
)


def test_matricule_solde_slash_only_is_empty() -> None:
    for v in ("/", " / ", "  /  ", None, "", "   "):
        assert matricule_solde_is_empty(v)
        assert normalize_matricule_solde(v) is None
        assert format_matricule_solde_display(v) == "—"


def test_matricule_solde_real_value_preserved() -> None:
    assert not matricule_solde_is_empty("12345/A")
    assert normalize_matricule_solde("12345/A") == "12345/A"
    assert format_matricule_solde_display("12345/A") == "12345/A"
