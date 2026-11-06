"""Vidage complet des données métier MySQL — conservation des comptes utilisateurs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import (
    Settings,
    detaches_config_path,
    liste_layout_config_path,
    project_root,
)
from sme_gend.config.user_paths import user_application_data_dir
from sme_gend.config.view_state import view_state_file
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.repositories.mysql_pool import invalidate_mysql_pool
from sme_gend.services.app_config_store import KEY_AUTH, load_config_dict, save_config_dict
from sme_gend.services.config_migration_v2 import KEY_MIGRATION_V2

_log = logging.getLogger(__name__)

_PRESERVE_LOCAL_FILES = frozenset({"paths.json", "auth_config.json"})


def _load_auth_payload(settings: Settings) -> dict[str, Any] | None:
    raw = load_config_dict(settings, KEY_AUTH)
    if raw:
        return dict(raw)
    from sme_gend.config.auth_config import AuthConfig, auth_config_file

    local = auth_config_file()
    if not local.is_file():
        return None
    try:
        data = json.loads(local.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return AuthConfig.from_dict(data).to_dict()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        _log.warning("Lecture auth locale : %s", exc)
    return None


def _table_row_counts(settings: Settings) -> dict[str, int]:
    out: dict[str, int] = {}
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            from sme_gend.services.database_maintenance import INTERNAL_TABLES
            tables = [str(r[0]) for r in cur.fetchall() if str(r[0]) not in INTERNAL_TABLES]
            for name in tables:
                cur.execute(f"SELECT COUNT(*) FROM `{name}`")
                row = cur.fetchone()
                out[name] = int(row[0] or 0) if row else 0
    return out


def _truncate_all_mysql_tables(settings: Settings) -> list[str]:
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            )
            from sme_gend.services.database_maintenance import INTERNAL_TABLES
            tables = [str(r[0]) for r in cur.fetchall() if str(r[0]) not in INTERNAL_TABLES]
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for name in tables:
                cur.execute(f"TRUNCATE TABLE `{name}`")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")
    return tables


def _reset_local_json_configs() -> list[str]:
    touched: list[str] = []
    empty_layout = {
        "version": 1,
        "template_sheet": "Liste",
        "output_sheet": "Liste_SME",
        "header_row": 3,
        "style_row_section": 4,
        "style_row_sub": 5,
        "style_row_data": 6,
        "ncols": 18,
        "sections": [],
    }
    writes: list[tuple[Path, dict[str, Any]]] = [
        (liste_layout_config_path(), empty_layout),
        (detaches_config_path(), {"version": 1, "structures": []}),
        (project_root() / "data" / "limite_age_config.json", {"version": 1, "grades": []}),
        (project_root() / "data" / "soit_transmis_config.json", {"version": 1, "runs": []}),
    ]
    for path, payload in writes:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            touched.append(str(path))
        except OSError as exc:
            _log.warning("Reset %s : %s", path, exc)

    vs = view_state_file()
    try:
        vs.parent.mkdir(parents=True, exist_ok=True)
        vs.write_text("{}", encoding="utf-8")
        touched.append(str(vs))
    except OSError as exc:
        _log.warning("Reset view_state : %s", exc)
    return touched


def _clear_jsonl_and_logs() -> list[str]:
    cleared: list[str] = []
    roots = [
        project_root() / "data",
        user_application_data_dir(),
    ]
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for pattern in ("*.jsonl",):
            for fp in root.glob(pattern):
                if fp in seen:
                    continue
                seen.add(fp)
                try:
                    fp.write_text("", encoding="utf-8")
                    cleared.append(str(fp))
                except OSError as exc:
                    _log.warning("Vidage %s : %s", fp, exc)
    return cleared


def _clear_personnel_photos(settings: Settings) -> int:
    from sme_gend.config.user_paths import resolved_personnel_media_root

    n = 0
    roots = [
        resolved_personnel_media_root(),
        project_root() / "data" / "personnel_media",
    ]
    seen: set[Path] = set()
    for root in roots:
        photos = root / "photos"
        if not photos.is_dir():
            continue
        try:
            resolved = photos.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        for fp in photos.rglob("*"):
            if fp.is_file():
                try:
                    fp.unlink()
                    n += 1
                except OSError as exc:
                    _log.warning("Suppression photo %s : %s", fp, exc)
    return n


def _restore_post_wipe_config(settings: Settings, auth_payload: dict[str, Any] | None) -> None:
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    AppConfigRepository(settings).ensure_tables()
    if auth_payload:
        save_config_dict(settings, KEY_AUTH, auth_payload, updated_by="wipe_operational")
    save_config_dict(
        settings,
        KEY_MIGRATION_V2,
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "stats": {},
            "note": "post_wipe_skip_local_reimport",
        },
        updated_by="wipe_operational",
    )
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService

    ws = SmeWorkspaceService(settings)
    preferences = ws.load_workspace_payload()
    preferences.update({
        "editeur_defaut": "", "jour_cloture": None,
        "prompt_mois_au_demarrage": True, "presences_mode": "present",
    })
    ws.save_workspace_payload(preferences)


def wipe_all_except_auth_users(settings: Settings) -> dict[str, Any]:
    """
    Supprime toutes les données MySQL et remet à zéro les fichiers métier locaux.

    Conservé : comptes / rôles / paramètres d'auth (``app_config.auth_config``),
    ``paths.json`` et ``auth_config.json`` locaux s'ils existent.
    """
    auth_payload = _load_auth_payload(settings)
    n_users = 0
    if auth_payload and isinstance(auth_payload.get("users"), list):
        n_users = len(auth_payload["users"])

    before = _table_row_counts(settings)
    tables = _truncate_all_mysql_tables(settings)
    _restore_post_wipe_config(settings, auth_payload)
    invalidate_mysql_pool(settings)

    local_json = _reset_local_json_configs()
    cleared_logs = _clear_jsonl_and_logs()
    photos_removed = _clear_personnel_photos(settings)

    after = _table_row_counts(settings)
    non_zero = {k: v for k, v in after.items() if v > 0}

    return {
        "tables_truncated": len(tables),
        "auth_users_preserved": n_users,
        "auth_restored": auth_payload is not None,
        "rows_before_total": sum(before.values()),
        "rows_after_total": sum(after.values()),
        "non_zero_tables_after": non_zero,
        "local_json_reset": local_json,
        "jsonl_cleared": cleared_logs,
        "photos_removed": photos_removed,
    }
