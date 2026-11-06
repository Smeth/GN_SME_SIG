"""Hiérarchie subdivisions (Access)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.repositories.access_connection import AccessConnection

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
INNER JOIN TabSectrois AS t ON t.CodeD = d.CodeD
ORDER BY u.CodeU, d.PriOutD, d.CodeD, t.PriOutT, t.CodeT
"""


class AccessSubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL_FLAT)
            return [SubdivisionFlatRow.from_sequence(r) for r in cur.fetchall()]
