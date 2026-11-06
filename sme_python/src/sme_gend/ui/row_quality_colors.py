"""Couleurs de surlignage des lignes (qualité données) — lisibles en thème clair et sombre."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


@dataclass(frozen=True, slots=True)
class RowHighlight:
    bg: QColor
    fg: QColor


def _pair(light_bg: str, light_fg: str, dark_bg: str, dark_fg: str) -> RowHighlight:
    from sme_gend.ui.theme import is_dark

    if is_dark():
        return RowHighlight(QColor(dark_bg), QColor(dark_fg))
    return RowHighlight(QColor(light_bg), QColor(light_fg))


def highlight_sot_gap() -> RowHighlight:
    """Fiche dont le grade ou le diplôme diffère du dossier de l'agent."""
    return _pair("#fde8e8", "#7f1d1d", "#4a2c32", "#fecdd3")


def highlight_fk_unresolved() -> RowHighlight:
    """FK carrière non résolue (référentiel manquant)."""
    return _pair("#e8eefd", "#1e3a8a", "#243047", "#bfdbfe")


def highlight_modified() -> RowHighlight:
    """Ligne modifiée durant la session (édition SME)."""
    return _pair("#d4edda", "#14532d", "#1e3a2f", "#bbf7d0")


def highlight_incomplete() -> RowHighlight:
    """Données obligatoires manquantes (grade, structure…)."""
    return _pair("#fff3cd", "#854d0e", "#3d3520", "#fde68a")


def highlight_find_match() -> RowHighlight:
    """Occurrence trouvée (Rechercher / Remplacer) — ligne non courante."""
    return _pair("#fff9c4", "#713f12", "#3d3520", "#fde68a")


def highlight_find_current() -> RowHighlight:
    """Occurrence courante (Rechercher / Remplacer)."""
    return _pair("#ffe082", "#e65100", "#5c4a2a", "#ffd54f")
