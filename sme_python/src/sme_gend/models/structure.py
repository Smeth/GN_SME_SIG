"""Table `TabStructure`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class StructureRow:
    code: str
    designation: str
    ordre_tri: int | None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> StructureRow:
        return cls(
            code=str(row[0] or "").strip(),
            designation=str(row[1] or "").strip(),
            ordre_tri=int(row[2]) if row[2] is not None else None,
        )
