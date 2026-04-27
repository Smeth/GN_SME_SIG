"""Lecture / écriture référentiel structures (SQLite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.sqlite_common import apply_sqlite_schema


class SqliteStructureRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqliteStructureRepository")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))

    def fetch_all(self) -> list[StructureRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT code, designation, oder_tri FROM tab_structure "
                "ORDER BY oder_tri, code"
            )
            return [StructureRow.from_sequence(r) for r in cur.fetchall()]

    def fetch_by_code(self, code: str) -> StructureRow | None:
        c = (code or "").strip()
        if not c:
            return None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT code, designation, oder_tri FROM tab_structure WHERE code = ?",
                (c,),
            )
            r = cur.fetchone()
            return StructureRow.from_sequence(r) if r else None

    def insert(self, code: str, designation: str, ordre_tri: int | None) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tab_structure (code, designation, oder_tri) VALUES (?, ?, ?)",
                (code.strip(), designation.strip(), ordre_tri),
            )
            conn.commit()

    def update(self, code: str, designation: str, ordre_tri: int | None) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tab_structure SET designation = ?, oder_tri = ? WHERE code = ?",
                (designation.strip(), ordre_tri, code.strip()),
            )
            conn.commit()

    def delete(self, code: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tab_structure WHERE code = ?", (code.strip(),))
            conn.commit()

    def rename_code(self, old_code: str, new_code: str) -> None:
        old_c = (old_code or "").strip()
        new_c = (new_code or "").strip()
        if not old_c or not new_c or old_c == new_c:
            raise ValueError("Codes invalides.")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tab_structure SET code = ? WHERE code = ?",
                (new_c, old_c),
            )
            if cur.rowcount == 0:
                raise ValueError(f"Aucune ligne tab_structure pour le code « {old_c} ».")
            conn.commit()
