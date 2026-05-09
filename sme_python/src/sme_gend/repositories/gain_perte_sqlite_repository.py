"""Repository SQLite pour ``tab_gain_perte``."""

from __future__ import annotations

from pathlib import Path

from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.sqlite_common import BaseSqliteRepository


def _row(r: object) -> GainPerteRow:
    def g(k: str):  # type: ignore[return]
        try:
            return r[k]  # type: ignore[index]
        except (KeyError, TypeError):
            return None

    def s(v: object) -> str | None:
        if v is None:
            return None
        x = str(v).strip()
        return x or None

    return GainPerteRow(
        id=str(g("id") or "").strip(),
        grade=s(g("grade")),
        nom=s(g("nom")),
        objet=s(g("objet")),
        cdsme=s(g("cdsme")),
        observ=s(g("observ")),
    )


class SqliteGainPerteRepository(BaseSqliteRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)

    def fetch_all(self) -> list[GainPerteRow]:
        try:
            import sqlite3
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute("SELECT id, grade, nom, objet, cdsme, observ FROM tab_gain_perte")
                return [_row(r) for r in cur.fetchall()]
        except Exception:
            return []

    def fetch_for_cdsme(self, cdsme: str) -> list[GainPerteRow]:
        try:
            import sqlite3
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, grade, nom, objet, cdsme, observ FROM tab_gain_perte WHERE cdsme = ?",
                    (cdsme,),
                )
                return [_row(r) for r in cur.fetchall()]
        except Exception:
            return []
