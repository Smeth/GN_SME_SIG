"""Modèle pour le référentiel des positions administratives (tab_padmin)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class PadminRefRow:
    """Une position administrative référencée."""

    libelle: str            # clé primaire = valeur stockée dans liste_generale.padmin
    compte_comme_present: bool
    ordre_tri: int | None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> PadminRefRow:
        return cls(
            libelle=str(row[0] or "").strip(),
            compte_comme_present=bool(row[1]),
            ordre_tri=int(row[2]) if row[2] is not None else None,
        )
