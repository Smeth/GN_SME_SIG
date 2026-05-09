"""Fonctions distinctes depuis liste_generale (SQLite)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.repositories.sqlite_common import BaseSqliteRepository

SQL = """
SELECT DISTINCT fonction
FROM liste_generale
WHERE fonction IS NOT NULL AND trim(fonction) <> ''
ORDER BY fonction
"""


class SqliteFonctionsRepository(BaseSqliteRepository):
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis")
        super().__init__(Path(settings.sqlite_liste_path).expanduser().resolve())

    def fetch_distinct(self) -> list[str]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]
