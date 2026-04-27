"""Lecture / écriture référentiel grades (SQLite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.repositories.sqlite_common import apply_sqlite_schema


class SqliteGradeRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqliteGradeRepository")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))

    def fetch_all(self) -> list[GradeRefRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT code_g, grade, pref_ord, effectif_g FROM tab_grade "
                "ORDER BY pref_ord, code_g"
            )
            return [GradeRefRow.from_sequence(r) for r in cur.fetchall()]

    def insert(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tab_grade (code_g, grade, pref_ord, effectif_g) VALUES (?, ?, ?, ?)",
                (code.strip(), libelle.strip(), pref_ord, effectif_g),
            )
            conn.commit()

    def update(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tab_grade SET grade = ?, pref_ord = ?, effectif_g = ? WHERE code_g = ?",
                (libelle.strip(), pref_ord, effectif_g, code.strip()),
            )
            conn.commit()

    def delete(self, code: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tab_grade WHERE code_g = ?", (code.strip(),))
            conn.commit()
