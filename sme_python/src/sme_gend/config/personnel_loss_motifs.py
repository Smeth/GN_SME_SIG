"""Motifs de sortie (perte) lors de la suppression d'une fiche personnel."""

from __future__ import annotations

# Aligné sur Gains & pertes / TabGainPerte Access
PERSONNEL_LOSS_MOTIFS: tuple[str, ...] = (
    "DECES",
    "DECEDE",
    "REMIS MIN DEFENSE",
    "RETRAITE",
    "DEMISSION",
    "ERREUR",
    "Non signalé",
    "AUTRE",
)
