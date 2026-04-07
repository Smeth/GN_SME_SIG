"""Lecture hiérarchie subdivisions (Secun / Secdeux / Sectrois)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection

# Jointure : niveau 1 (U) → 2 (D) → 3 (T)
SQL_FLAT = """
SELECT
    u.CodeU,
    u.SecuDesign,
    d.CodeD,
    d.SecdDesign,
    t.CodeT,
    t.SectDesign
FROM (TabSecun AS u
INNER JOIN TabSecdeux AS d ON u.CodeU = d.CodeU)
INNER JOIN TabSectrois AS t ON d.CodeD = t.CodeD
ORDER BY u.CodeU, d.PriOutD, d.CodeD, t.PriOutT, t.CodeT
"""


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


class SubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL_FLAT)
            return [SubdivisionFlatRow.from_sequence(r) for r in cur.fetchall()]
