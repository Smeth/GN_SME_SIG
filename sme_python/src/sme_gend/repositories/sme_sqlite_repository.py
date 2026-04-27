"""Lecture / écriture périodes SME (SQLite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.sqlite_common import apply_sqlite_schema


class SqliteSmeRepository:
    def __init__(self, settings: Settings) -> None:
        if settings.sqlite_liste_path is None:
            raise ValueError("sqlite_liste_path requis pour SqliteSmeRepository")
        self._path = Path(settings.sqlite_liste_path).expanduser().resolve()
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))

    def table_exists(self) -> bool:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tab_sme' LIMIT 1"
            )
            return cur.fetchone() is not None

    def fetch_all(self) -> list[SmeRow]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT codsme, mois, statut FROM tab_sme ORDER BY mois"
            )
            return [SmeRow.from_sequence(r) for r in cur.fetchall()]

    def insert(self, codsme: str, mois: str, statut: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tab_sme (codsme, mois, statut) VALUES (?, ?, ?)",
                (codsme, mois, statut),
            )
            conn.commit()

    def update(self, codsme: str, mois: str, statut: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tab_sme SET mois = ?, statut = ? WHERE codsme = ?",
                (mois, statut, codsme),
            )
            conn.commit()

    def delete(self, codsme: str) -> None:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM tab_sme WHERE codsme = ?", (codsme,))
            conn.commit()
