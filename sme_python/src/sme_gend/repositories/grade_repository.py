"""Lecture `TabGrade`."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.repositories.access_connection import AccessConnection

SQL = """
SELECT CodeG, Grade, PrefOrd, EffectifG
FROM TabGrade
ORDER BY PrefOrd, CodeG
"""


class GradeRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_all(self) -> list[GradeRefRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [GradeRefRow.from_sequence(r) for r in cur.fetchall()]
