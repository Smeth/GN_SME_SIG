"""Modèle pour la table Access ``TabGainPerte`` (gains et pertes mensuels)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GainPerteRow:
    """Une ligne de TabGainPerte."""

    id: str
    grade: str | None
    nom: str | None
    objet: str | None       # "GAIN " ou "PERTE " (avec espace possible)
    cdsme: str | None       # format MMYYYY ex. "012026"
    observ: str | None      # "DECES", "AJOUT", "ERREUR", …

    @property
    def objet_norm(self) -> str:
        return (self.objet or "").strip().upper()

    @property
    def is_gain(self) -> bool:
        return self.objet_norm == "GAIN"

    @property
    def is_perte(self) -> bool:
        return self.objet_norm == "PERTE"

    @property
    def is_deces(self) -> bool:
        return self.is_perte and (self.observ or "").strip().upper() == "DECES"

    @property
    def observ_display(self) -> str:
        return (self.observ or "").strip() or "—"

    @property
    def objet_display(self) -> str:
        n = self.objet_norm
        if n == "GAIN":
            return "Gain"
        if n == "PERTE":
            return "Perte"
        return (self.objet or "").strip() or "—"
