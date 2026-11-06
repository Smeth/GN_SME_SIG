"""Table `TabStructure`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class StructureRow:
    code: str
    designation: str
    ordre_tri: int | None
    famille_unite: str | None = None

    write_token: tuple[str, str, str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> StructureRow:
        fam = None
        if len(row) > 3 and row[3] is not None:
            s = str(row[3]).strip()
            fam = s or None
        return cls(
            code=str(row[0] or "").strip(),
            designation=str(row[1] or "").strip(),
            ordre_tri=int(row[2]) if row[2] is not None else None,
            famille_unite=fam,
            write_token=("structure", str(row[0] or "").strip(), str(row[4])) if len(row)>4 and row[4] is not None else None,
        )
