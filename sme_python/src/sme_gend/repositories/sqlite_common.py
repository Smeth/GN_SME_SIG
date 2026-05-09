"""Schéma SQLite partagé (liste générale + référentiels + satellites)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.config.settings import project_root


class BaseSqliteRepository:
    """Base pour les dépôts SQLite : résolution du chemin, schéma, connexion."""

    def __init__(self, path: Path) -> None:
        self._path = path
        apply_sqlite_schema(self._path)

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(str(self._path))


def sqlite_schema_file() -> Path:
    return project_root() / "data" / "schema" / "liste_generale_sqlite.sql"


# ── Schéma + migrations ───────────────────────────────────────────────────────

# Incrémenté à chaque nouvelle migration ajoutée dans _run_migration_step().
_SCHEMA_VERSION = 3


def apply_sqlite_schema(db_path: Path) -> None:
    p = sqlite_schema_file()
    if not p.is_file():
        raise FileNotFoundError(f"Schéma SQLite introuvable : {p}")
    sql = p.read_text(encoding="utf-8")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(sql)
    _apply_migrations(db_path)


def _run_migration_step(conn: sqlite3.Connection, version: int) -> None:
    """Applique UNE migration identifiée par son numéro de version."""
    if version == 1:
        # Renommage typo oder_tri → ordre_tri (migration historique)
        for table in ("liste_generale", "tab_structure"):
            try:
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            except sqlite3.DatabaseError:
                continue
            if "oder_tri" in cols and "ordre_tri" not in cols:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN oder_tri TO ordre_tri")
    elif version == 2:
        # Activation WAL pour de meilleures performances concurrentes (idempotent)
        conn.execute("PRAGMA journal_mode = WAL")
    elif version == 3:
        # Renommage index : idx_liste_generale_oder_tri → idx_liste_generale_ordre_tri
        # (la colonne a été renommée en v1 ; SQLite met à jour le contenu mais pas le nom)
        conn.execute("DROP INDEX IF EXISTS idx_liste_generale_oder_tri")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_liste_generale_ordre_tri "
            "ON liste_generale(ordre_tri)"
        )
    # --- Prochaine version : ajouter elif version == 4: ... ici ---


def _apply_migrations(db_path: Path) -> None:
    """Applique toutes les migrations manquantes en utilisant PRAGMA user_version."""
    if not db_path.is_file():
        return
    with sqlite3.connect(str(db_path)) as conn:
        current: int = conn.execute("PRAGMA user_version").fetchone()[0] or 0
        if current >= _SCHEMA_VERSION:
            return
        for v in range(current + 1, _SCHEMA_VERSION + 1):
            _run_migration_step(conn, v)
        conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        conn.commit()


def migrate_oder_tri(db_path: Path) -> None:
    """Compatibilité ascendante — utilise désormais _apply_migrations."""
    _apply_migrations(db_path)


def connect_sqlite_liste(settings_path: Path) -> sqlite3.Connection:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    migrate_oder_tri(settings_path)
    return sqlite3.connect(str(settings_path))
