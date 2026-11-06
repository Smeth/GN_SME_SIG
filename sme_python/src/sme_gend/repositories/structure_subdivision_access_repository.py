"""Couples (Structure, SubArme) distincts depuis ListeGenerale (Access)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection

SQL = """
SELECT DISTINCT Structure, SubArme
FROM ListeGenerale
WHERE Structure IS NOT NULL AND Structure <> ''
ORDER BY Structure, SubArme
"""


class AccessStructureSubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_distinct_pairs(self) -> list[tuple[str, str]]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [
                (str(r[0] or "").strip(), str(r[1] or "").strip())
                for r in cur.fetchall()
            ]
