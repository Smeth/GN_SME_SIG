"""Schéma SQLite partagé (liste générale + référentiels + satellites)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import project_root


def sqlite_schema_file() -> Path:
    return project_root() / "data" / "schema" / "liste_generale_sqlite.sql"


def apply_sqlite_schema(db_path: Path) -> None:
    p = sqlite_schema_file()
    if not p.is_file():
        raise FileNotFoundError(f"Schéma SQLite introuvable : {p}")
    sql = p.read_text(encoding="utf-8")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)


def connect_sqlite_liste(settings_path: Path) -> sqlite3.Connection:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(str(settings_path))
