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

    def insert_row(self, row: GainPerteRow) -> None:
        import sqlite3
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tab_gain_perte (id, grade, nom, objet, cdsme, observ) VALUES (?,?,?,?,?,?)",
                (
                    str(row.id or "").strip(),
                    row.grade,
                    row.nom,
                    row.objet,
                    row.cdsme,
                    row.observ,
                ),
            )
            conn.commit()

    def update_row(
        self,
        row: GainPerteRow,
        *,
        where_id: str,
        where_cdsme: str | None,
    ) -> None:
        import sqlite3
        w_id = str(where_id or "").strip()
        w_cd = (where_cdsme or "").strip() or None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE tab_gain_perte
                SET id = ?, grade = ?, nom = ?, objet = ?, cdsme = ?, observ = ?
                WHERE id = ? AND IFNULL(trim(cdsme), '') = IFNULL(trim(?), '')
                """,
                (
                    str(row.id or "").strip(),
                    row.grade,
                    row.nom,
                    row.objet,
                    row.cdsme,
                    row.observ,
                    w_id,
                    w_cd if w_cd is not None else "",
                ),
            )
            conn.commit()

    def delete_row(self, row_id: str, cdsme: str | None) -> None:
        import sqlite3
        w_id = str(row_id or "").strip()
        w_cd = (cdsme or "").strip() or None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM tab_gain_perte WHERE id = ? AND IFNULL(trim(cdsme), '') = IFNULL(trim(?), '')",
                (w_id, w_cd if w_cd is not None else ""),
            )
            conn.commit()
