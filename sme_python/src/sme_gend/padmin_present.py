"""Critère « compte comme présent » — isolé pour éviter les imports circulaires services ↔ repositories.

Modes pilotés par ``presences_mode`` (``SmeContext``) :

* ``"present"``       — uniquement les positions contenant le mot « présent » (défaut liste des présents)
* ``"stage_interne"`` — uniquement les positions « stage interne »
* ``"les_deux"``      — présent **ou** stage interne (option d’affichage)
"""

from __future__ import annotations

# Modes valides
PRESENCES_MODES: tuple[str, ...] = ("present", "stage_interne", "les_deux")


def _is_present(padmin: str | None) -> bool:
    """Vrai si la position administrative contient le mot « présent » (accents neutralisés)."""
    if padmin is None:
        return False
    raw = str(padmin).strip()
    if not raw:
        return False
    norm = raw.lower().replace("é", "e").replace("è", "e")
    return "present" in norm


def _is_stage_interne(padmin: str | None) -> bool:
    """Vrai si la position administrative est exactement « stage interne »."""
    if padmin is None:
        return False
    raw = str(padmin).strip()
    if not raw:
        return False
    collapsed = " ".join(raw.split()).casefold().replace("é", "e")
    return collapsed in ("stageinterne", "stage interne")


def padmin_counts_as_present(
    padmin: str | None,
    mode: str = "present",
) -> bool:
    """
    Retourne True si la position administrative correspond au mode choisi.

    :param padmin: valeur de la colonne Padmin en base.
    :param mode:  ``"present"`` (défaut), ``"stage_interne"`` ou ``"les_deux"``.
    """
    if mode == "present":
        return _is_present(padmin)
    if mode == "stage_interne":
        return _is_stage_interne(padmin)
    # "les_deux" ou valeur inconnue → comportement historique
    return _is_present(padmin) or _is_stage_interne(padmin)
