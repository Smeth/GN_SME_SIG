"""Table `TabGrade`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class GradeRefRow:
    code: str
    libelle: str
    pref_ord: int | None
    effectif_g: int | None

    write_token: tuple[str, str, str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> GradeRefRow:
        return cls(
            code=str(row[0] or "").strip(),
            libelle=str(row[1] or "").strip(),
            pref_ord=int(row[2]) if row[2] is not None else None,
            effectif_g=int(row[3]) if row[3] is not None else None,
            write_token=("grade", str(row[0] or "").strip(), str(row[4])) if len(row)>4 and row[4] is not None else None,
        )
