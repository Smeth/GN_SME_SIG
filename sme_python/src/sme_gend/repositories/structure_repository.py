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

    def insert(self, code: str, designation: str, ordre_tri: int | None) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO TabStructure (code, designation, OderTri) VALUES (?, ?, ?)",
                (code.strip(), designation.strip(), ordre_tri),
            )

    def update(self, code: str, designation: str, ordre_tri: int | None) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE TabStructure SET designation = ?, OderTri = ? WHERE code = ?",
                (designation.strip(), ordre_tri, code.strip()),
            )

    def delete(self, code: str) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TabStructure WHERE code = ?", (code.strip(),))
