"""Modèle pour le référentiel des positions administratives (ref_position_admin)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class PadminRefRow:
    """Une position administrative référencée."""

    libelle: str            # Libellé affiché ; ref_id conserve l'identifiant MySQL stable.
    compte_comme_present: bool
    ordre_tri: int | None
    write_token: tuple[str, str, str] | None = field(default=None, repr=False, compare=False)
    ref_id: int | None = field(default=None, compare=False)

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> PadminRefRow:
        return cls(
            libelle=str(row[0] or "").strip(),
            compte_comme_present=bool(row[1]),
            ordre_tri=int(row[2]) if row[2] is not None else None,
            write_token=("padmin", str(row[0]).strip(), str(row[3])) if len(row) > 3 and row[3] else None,
            ref_id=int(row[4]) if len(row) > 4 else None,
        )
