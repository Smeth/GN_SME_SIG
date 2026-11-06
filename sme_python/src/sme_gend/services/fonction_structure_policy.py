"""Rattachement fonction ↔ structure.

Toutes les structures imposent une fonction du référentiel lié, sauf le
contrôle spécial (CS-DPF) et les détachés : leurs fonctions ne sont ni
rattachées ni connues à l'avance.
"""

from __future__ import annotations

_UNLINKED_FOLDED = frozenset(
    {
        "CS DPF",
        "CONTROLE SPECIAL",
        "CONTROLE SPECIALE",
        "CONTROLE SPECIAL DPF",
        "LES DETACHE",
        "LES DETACHES",
        "DETACHE",
        "DETACHES",
    }
)


def _fold(value: str | None) -> str:
    s = (value or "").strip().upper()
    for src, dst in (
        ("É", "E"),
        ("È", "E"),
        ("Ê", "E"),
        ("Ë", "E"),
        ("À", "A"),
        ("Â", "A"),
        ("Ù", "U"),
        ("Û", "U"),
        ("Ô", "O"),
        ("Î", "I"),
        ("Ï", "I"),
        ("Ç", "C"),
    ):
        s = s.replace(src, dst)
    s = s.replace("-", " ").replace("_", " ").replace("/", " ")
    return " ".join(s.split())


def fold_label(value: str | None) -> str:
    """Comparaison de libellés : casse, accents et tirets ignorés."""
    return _fold(value)


def structure_fonctions_non_liees(structure_code: str | None) -> bool:
    """True pour le contrôle spécial et les détachés."""
    return _fold(structure_code) in _UNLINKED_FOLDED
