"""Fonctions distinctes depuis ListeGenerale (Access)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection

SQL = """
SELECT DISTINCT Fonction
FROM ListeGenerale
WHERE Fonction IS NOT NULL AND Fonction <> ''
ORDER BY Fonction
"""


class AccessFonctionsRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_distinct(self) -> list[str]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]
