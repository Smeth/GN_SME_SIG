"""Liste générale dans SQLite (migration depuis Access) — PK interne + ``id_acces``."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sqlite import (
    PersonnelSqliteRow,
    sqlite_pk_int_from_row_id,
    sqlite_update_set_tuple_from_liste_row,
)
from sme_gend.padmin_present import padmin_counts_as_present
from sme_gend.repositories.sqlite_common import apply_sqlite_schema

_SELECT_ROW = """
SELECT id, id_acces, noms, date_n, lieu_n, grade, date_grade, date_service,
       ndecision, matmilitaire, matsolde, padmin, fonction, sub_arme,
       diplome, date_obtention, structure, obs, ordre_tri, presseance
FROM liste_generale
"""


class SqliteListeGeneraleRepository:
    """Accès à la table ``liste_generale`` (fichier SQLite)."""

    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqliteListeGeneraleRepository")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))

    def _ensure_schema(self) -> None:
        apply_sqlite_schema(self._path)

    def _row_to_liste(self, row: tuple) -> ListeGeneraleRow:
        ps = PersonnelSqliteRow.from_sqlite_sequence(row)
        return ps.to_liste_generale_row()

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        rid = (row_id or "").strip()
        if not rid:
            return None
        try:
            pk = int(rid)
        except ValueError:
            return None
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"{_SELECT_ROW.strip()} WHERE id = ?", (pk,))
            r = cur.fetchone()
            if not r:
                return None
            return self._row_to_liste(r)

    def fetch_all(self) -> list[ListeGeneraleRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"{_SELECT_ROW.strip()} ORDER BY ordre_tri, id")
            return [self._row_to_liste(r) for r in cur.fetchall()]

    def fetch_presents(self) -> list[ListeGeneraleRow]:
        """Personnels comptés comme « présents » : uniquement Padmin « présent » (pas stage interne, etc.)."""
        rows = self.fetch_all()
        return [r for r in rows if padmin_counts_as_present(r.position_admin, "present")]

    def fetch_distinct_padmin(self) -> list[str]:
        q = """
        SELECT DISTINCT padmin FROM liste_generale
        WHERE padmin IS NOT NULL AND trim(padmin) <> ''
        ORDER BY padmin
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme(self) -> list[str]:
        q = """
        SELECT DISTINCT sub_arme FROM liste_generale
        WHERE sub_arme IS NOT NULL AND trim(sub_arme) <> ''
        ORDER BY sub_arme
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(q)
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def fetch_distinct_subarme_for_structure(self, structure_code: str) -> list[str]:
        q = """
        SELECT DISTINCT sub_arme
        FROM liste_generale
        WHERE structure = ? AND sub_arme IS NOT NULL AND trim(sub_arme) <> ''
        ORDER BY sub_arme
        """
        code = (structure_code or "").strip()
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(q, (code,))
            return [str(r[0]).strip() for r in cur.fetchall() if r[0]]

    def update_row(self, row: ListeGeneraleRow) -> None:
        pk = sqlite_pk_int_from_row_id(row.id)
        set_vals = sqlite_update_set_tuple_from_liste_row(row)
        sql = """
        UPDATE liste_generale SET
            noms = ?, date_n = ?, lieu_n = ?, grade = ?, date_grade = ?, date_service = ?,
            ndecision = ?, matmilitaire = ?, matsolde = ?, padmin = ?, fonction = ?,
            sub_arme = ?, diplome = ?, date_obtention = ?, structure = ?, obs = ?,
            ordre_tri = ?, presseance = ?, updated_at = datetime('now')
        WHERE id = ?
        """
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, (*set_vals, pk))
            if cur.rowcount == 0:
                raise ValueError(f"Aucune ligne mise à jour pour id = {pk!r}")

    def insert_row(self, row: ListeGeneraleRow) -> str:
        """Insère une nouvelle ligne ; retourne l'id (clé primaire SQLite) sous forme de str."""
        sql = """
        INSERT INTO liste_generale (
            id_acces, noms, date_n, lieu_n, grade, date_grade, date_service,
            ndecision, matmilitaire, matsolde, padmin, fonction, sub_arme,
            diplome, date_obtention, structure, obs, ordre_tri, presseance
        ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            row.noms, row.date_naissance, row.lieu_naissance,
            row.grade, row.date_grade, row.date_service, row.ndecision,
            row.matricule_militaire, row.matricule_solde, row.position_admin,
            row.fonction, row.subdivision_arme, row.diplome, row.date_obtention,
            row.structure, row.obs, row.ordre_tri, row.presseance,
        )
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return str(cur.lastrowid)

    def delete_row(self, row_id: str) -> None:
        pk = int(row_id)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM liste_generale WHERE id = ?", (pk,))
            if cur.rowcount == 0:
                raise ValueError(f"Aucune ligne supprimée pour id = {pk!r}")
            conn.commit()

    def update_structure_column(self, old_code: str, new_code: str) -> int:
        o = (old_code or "").strip()
        n = (new_code or "").strip()
        if not o or not n:
            return 0
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE liste_generale SET structure = ?, updated_at = datetime('now') "
                "WHERE structure = ?",
                (n, o),
            )
            return int(cur.rowcount or 0)

    def rename_subarme_for_structure(
        self, structure_code: str, old_label: str, new_label: str
    ) -> int:
        sc = (structure_code or "").strip()
        if not sc:
            return 0
        old_l = (old_label or "").strip()
        new_l = (new_label or "").strip()
        old_empty = old_l in ("", "(Sans subdivision)")
        new_empty = new_l in ("", "(Sans subdivision)")
        new_val = None if new_empty else new_l

        with self._connect() as conn:
            cur = conn.cursor()
            if old_empty:
                cur.execute(
                    """
                    UPDATE liste_generale SET sub_arme = ?, updated_at = datetime('now')
                    WHERE structure = ? AND (sub_arme IS NULL OR trim(sub_arme) = '')
                    """,
                    (new_val, sc),
                )
            else:
                cur.execute(
                    """
                    UPDATE liste_generale SET sub_arme = ?, updated_at = datetime('now')
                    WHERE structure = ? AND sub_arme = ?
                    """,
                    (new_val, sc, old_l),
                )
            return int(cur.rowcount or 0)

    def bulk_import_from_access_rows(
        self, rows: list[ListeGeneraleRow]
    ) -> tuple[int, int]:
        """
        Importe ou met à jour depuis des lignes Access (``Id`` → ``id_acces``).
        Retourne (nombre d'insertions, nombre de mises à jour).
        """
        self._ensure_schema()
        insert_sql = """
        INSERT INTO liste_generale (
            id_acces, noms, date_n, lieu_n, grade, date_grade, date_service,
            ndecision, matmilitaire, matsolde, padmin, fonction, sub_arme,
            diplome, date_obtention, structure, obs, ordre_tri, presseance
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        update_sql = """
        UPDATE liste_generale SET
            noms = ?, date_n = ?, lieu_n = ?, grade = ?, date_grade = ?, date_service = ?,
            ndecision = ?, matmilitaire = ?, matsolde = ?, padmin = ?, fonction = ?,
            sub_arme = ?, diplome = ?, date_obtention = ?, structure = ?, obs = ?,
            ordre_tri = ?, presseance = ?, updated_at = datetime('now')
        WHERE id = ?
        """
        inserted = 0
        updated = 0
        with self._connect() as conn:
            cur = conn.cursor()
            for r in rows:
                ps = PersonnelSqliteRow.from_liste_generale_access(r)
                if not ps.id_acces:
                    continue
                cur.execute(
                    "SELECT id FROM liste_generale WHERE id_acces = ?",
                    (ps.id_acces,),
                )
                ex = cur.fetchone()
                if ex:
                    pk = int(ex[0])
                    lr = replace(ps.to_liste_generale_row(), id=str(pk))
                    cur.execute(
                        update_sql,
                        (*sqlite_update_set_tuple_from_liste_row(lr), pk),
                    )
                    updated += 1
                else:
                    cur.execute(insert_sql, ps.to_sql_params_upsert_without_pk())
                    inserted += 1
            conn.commit()
        return inserted, updated
