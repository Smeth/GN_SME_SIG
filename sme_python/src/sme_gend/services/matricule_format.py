"""Normalisation et affichage des matricules (conventions métier)."""

from __future__ import annotations


def matricule_solde_is_empty(value: str | None) -> bool:
    """
    True si le matricule solde est absent.

    Convention : une valeur composée uniquement de « / » (espaces tolérés)
    signifie « pas de matricule solde ».
    """
    s = (value or "").strip()
    if not s:
        return True
    return s.replace(" ", "") == "/"


def normalize_matricule_solde(value: str | None) -> str | None:
    """Retourne None si absent ou placeholder « / »."""
    if matricule_solde_is_empty(value):
        return None
    return (value or "").strip()


def format_matricule_solde_display(value: str | None, *, empty: str = "—") -> str:
    n = normalize_matricule_solde(value)
    return n if n else empty
