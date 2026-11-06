"""Schéma MySQL unifié (entité centrale : agent)."""

from __future__ import annotations

import threading
from pathlib import Path

from sme_gend.config.settings import Settings, project_root
from sme_gend.repositories.mysql_connection import (
    MySQLConnection,
    mysql_database_key,
    mysql_initialization_lock,
)


def mysql_schema_file() -> Path:
    return project_root() / "data" / "schema" / "mysql_smegend_unified.sql"


def _split_sql_statements(sql: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def apply_mysql_schema(settings: Settings) -> None:
    p = mysql_schema_file()
    if not p.is_file():
        raise FileNotFoundError(f"Schéma MySQL unifié introuvable : {p}")
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATABASE(), @@lower_case_table_names")
            selected = cur.fetchone()
            same_name = selected and selected[0] == settings.mysql_database
            insensitive_match = (selected and len(selected) > 1 and selected[1] in (1, 2)
                                 and str(selected[0]).casefold() == settings.mysql_database.casefold())
            if not (same_name or insensitive_match):
                raise RuntimeError("Initialisation annulée : la connexion cible une autre base.")
            for stmt in _split_sql_statements(sql):
                if stmt.lstrip().split(None, 1)[0].upper() == "USE":
                    continue
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "duplicate" in err or "already exists" in err or "1061" in err:
                        continue
                    if "duplicate entry" in err:
                        continue
                    if "can't drop" in err or "1091" in err:
                        continue
                    raise


def apply_mysql_operational_schema(settings: Settings) -> None:
    """Alias historique."""
    apply_mysql_schema(settings)


# Le mois consulté ne change pas la base ciblée : il ne doit donc pas provoquer
# une nouvelle exécution des migrations idempotentes à chaque vue d'archive.
_schema_ready_for: set[tuple[str, int, str]] = set()
_schema_init_lock = threading.RLock()
_schema_initializing = threading.local()


def ensure_mysql_schema(settings: Settings) -> None:
    """Crée les tables si absentes (sans réappliquer le script destructif)."""
    from sme_gend.repositories.mysql_connection import has_import_transaction

    if has_import_transaction(settings):
        return
    key = mysql_database_key(settings)
    with _schema_init_lock:
        if key in _schema_ready_for:
            return
        active = getattr(_schema_initializing, "keys", None)
        if active is None:
            active = set()
            _schema_initializing.keys = active
        if key in active:
            return  # A reference repository may be constructed by a migration.
        with mysql_initialization_lock(settings):
            active.add(key)
            try:
                mc = MySQLConnection(settings)
                with mc.connect(read_only=True) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            SELECT COUNT(*) FROM information_schema.tables
                            WHERE table_schema = DATABASE() AND table_name = 'agent'
                            """
                        )
                        schema_exists = bool(cur.fetchone()[0])
                from sme_gend.services.mysql_import_period import (
                    _ensure_mysql_migrations_unlocked,
                )

                if not schema_exists:
                    apply_mysql_schema(settings)
                _ensure_mysql_migrations_unlocked(settings)
                _schema_ready_for.add(key)
            finally:
                active.discard(key)


def invalidate_mysql_schema_cache() -> None:
    with _schema_init_lock:
        _schema_ready_for.clear()


class BaseMySQLRepository:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mc = MySQLConnection(settings)
        ensure_mysql_schema(settings)

    def _connect(self, *, read_only: bool = False):
        return self._mc.connect(read_only=read_only)
