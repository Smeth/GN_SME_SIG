"""Santé applicative : MySQL, sauvegardes."""

from __future__ import annotations

import time
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.config.user_paths import user_application_data_dir


def check_mysql_connectivity(settings: Settings) -> tuple[bool, str]:
    try:
        from sme_gend.repositories.mysql_connection import MySQLConnection

        mc = MySQLConnection(settings)
        t0 = time.perf_counter()
        with mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        ms = int((time.perf_counter() - t0) * 1000)
        return True, f"Connexion OK ({ms} ms) — {settings.mysql_host}/{settings.mysql_database}"
    except Exception as e:
        return False, str(e)


def backup_storage_dir() -> Path:
    """Dossier standard des sauvegardes SME (créé à la demande)."""
    d = user_application_data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backup_archives() -> list[Path]:
    """Archives ``sme_backup_*.zip`` triées par date (plus récent en premier)."""
    candidates: list[Path] = []
    for base in (backup_storage_dir(), user_application_data_dir(), Path.home() / "Documents"):
        if not base.is_dir():
            continue
        for p in base.glob("sme_backup*.zip"):
            if p.is_file():
                candidates.append(p.resolve())
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def find_latest_backup_zip() -> Path | None:
    """Archive de sauvegarde la plus récente."""
    archives = list_backup_archives()
    return archives[0] if archives else None


def backup_age_days(path: Path | None) -> float | None:
    if path is None or not path.is_file():
        return None
    import time

    age_sec = time.time() - path.stat().st_mtime
    return age_sec / 86400.0
