"""Couples (Structure, SubArme) distincts depuis liste_generale (SQLite)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.repositories.sqlite_common import BaseSqliteRepository

SQL = """
SELECT DISTINCT structure, sub_arme
FROM liste_generale
WHERE structure IS NOT NULL AND trim(structure) <> ''
ORDER BY structure, sub_arme
"""


class SqliteStructureSubdivisionRepository(BaseSqliteRepository):
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis")
        super().__init__(Path(settings.sqlite_liste_path).expanduser().resolve())

    def fetch_distinct_pairs(self) -> list[tuple[str, str]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [
                (str(r[0] or "").strip(), str(r[1] or "").strip())
                for r in cur.fetchall()
            ]
