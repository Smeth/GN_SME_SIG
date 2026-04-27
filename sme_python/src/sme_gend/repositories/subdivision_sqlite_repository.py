"""Hiérarchie subdivisions (SQLite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.subdivision_flat import SubdivisionFlatRow
from sme_gend.repositories.sqlite_common import apply_sqlite_schema

SQL_FLAT = """
SELECT
    u.code_u,
    u.secu_design,
    d.code_d,
    d.secd_design,
    t.code_t,
    t.sect_design
FROM tab_secun AS u
INNER JOIN tab_secdeux AS d ON u.code_u = d.code_u
INNER JOIN tab_sectrois AS t ON d.code_d = t.code_d
ORDER BY u.code_u, d.pri_out_d, d.code_d, t.pri_out_t, t.code_t
"""


class SqliteSubdivisionRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqliteSubdivisionRepository")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))

    def fetch_flat_hierarchy(self) -> list[SubdivisionFlatRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL_FLAT)
            return [SubdivisionFlatRow.from_sequence(r) for r in cur.fetchall()]
