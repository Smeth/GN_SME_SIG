"""Une ligne de la hiérarchie subdivisions (Secun / Secdeux / Sectrois)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(slots=True)
class SubdivisionFlatRow:
    code_u: str
    design_u: str
    code_d: str
    design_d: str
    code_t: str
    design_t: str

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> SubdivisionFlatRow:
        return cls(
            code_u=str(row[0] or "").strip(),
            design_u=str(row[1] or "").strip(),
            code_d=str(row[2] or "").strip(),
            design_d=str(row[3] or "").strip(),
            code_t=str(row[4] or "").strip(),
            design_t=str(row[5] or "").strip(),
        )
