"""Table `TabGrade`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class GradeRefRow:
    code: str
    libelle: str
    pref_ord: int | None
    effectif_g: int | None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> GradeRefRow:
        return cls(
            code=str(row[0] or "").strip(),
            libelle=str(row[1] or "").strip(),
            pref_ord=int(row[2]) if row[2] is not None else None,
            effectif_g=int(row[3]) if row[3] is not None else None,
        )
