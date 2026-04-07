"""Lecture `TabStructure`."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.access_connection import AccessConnection

SQL = """
SELECT code, designation, OderTri
FROM TabStructure
ORDER BY OderTri, code
"""


class StructureRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_all(self) -> list[StructureRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [StructureRow.from_sequence(r) for r in cur.fetchall()]
