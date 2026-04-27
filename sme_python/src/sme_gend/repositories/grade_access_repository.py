"""Lecture / écriture `TabGrade` (Access)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.repositories.access_connection import AccessConnection

SQL = """
SELECT CodeG, Grade, PrefOrd, EffectifG
FROM TabGrade
ORDER BY PrefOrd, CodeG
"""


class AccessGradeRepository:
    def __init__(self, settings: Settings) -> None:
        self._conn = AccessConnection(settings)

    def fetch_all(self) -> list[GradeRefRow]:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(SQL)
            return [GradeRefRow.from_sequence(r) for r in cur.fetchall()]

    def insert(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO TabGrade (CodeG, Grade, PrefOrd, EffectifG) VALUES (?, ?, ?, ?)",
                (code.strip(), libelle.strip(), pref_ord, effectif_g),
            )

    def update(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE TabGrade SET Grade = ?, PrefOrd = ?, EffectifG = ? WHERE CodeG = ?",
                (libelle.strip(), pref_ord, effectif_g, code.strip()),
            )

    def delete(self, code: str) -> None:
        with self._conn.connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM TabGrade WHERE CodeG = ?", (code.strip(),))
