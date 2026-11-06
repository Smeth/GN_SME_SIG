"""Migration one-shot : JSON / JSONL locaux → MySQL (app_config, audit_journal)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings, project_root
from sme_gend.config.user_paths import user_application_data_dir
from sme_gend.config.view_state import view_state_file
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.services.app_config_store import KEY_VIEW_STATE

_log = logging.getLogger(__name__)

KEY_MIGRATION_V2 = "migration_v2_complete"


def migration_v2_done(settings: Settings) -> bool:
    try:
        return AppConfigRepository(settings).has_key(KEY_MIGRATION_V2)
    except Exception:
        return False


def run_migration_v2(settings: Settings) -> dict[str, int]:
    """
    Importe view_state.json et les JSONL d'audit vers MySQL.
    Retourne des compteurs par type.
    """
    if migration_v2_done(settings):
        return {}
    repo = AppConfigRepository(settings)
    repo.ensure_tables()
    stats = {"view_state": 0, "personnel_audit": 0, "user_activity": 0, "doc_gen": 0}
    actor = "migration_v2"

    # view_state.json → app_config
    vs_path = view_state_file()
    if vs_path.is_file() and not repo.has_key(KEY_VIEW_STATE):
        try:
            raw = json.loads(vs_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                repo.set_json(KEY_VIEW_STATE, raw, updated_by=actor)
                stats["view_state"] = len(raw)
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("view_state : %s", exc)

    data_dir = project_root() / "data"
    if repo.table_exists("audit_journal"):
        stats["personnel_audit"] = _import_personnel_jsonl(repo, data_dir)
        stats["user_activity"] = _import_jsonl_file(
            repo, data_dir / "user_activity.jsonl", action="user_activity", module="system"
        )
        stats["doc_gen"] = _import_jsonl_file(
            repo, data_dir / "doc_gen_log.jsonl", action="doc_gen", module="doc_gen"
        )

    repo.set_json(
        KEY_MIGRATION_V2,
        {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
        },
        updated_by=actor,
    )
    _log.info("Migration v2 terminée : %s", stats)
    return stats


def _import_personnel_jsonl(repo: AppConfigRepository, data_dir: Path) -> int:
    n = 0
    files = sorted(data_dir.glob("personnel_audit_*.jsonl"))
    legacy = data_dir / "personnel_audit.jsonl"
    if legacy.is_file():
        files = [legacy] + [f for f in files if f != legacy]
    for path in files:
        n += _import_jsonl_file(repo, path, action=None, module="personnel", personnel_field="personnel_id")
    return n


def _import_jsonl_file(
    repo: AppConfigRepository,
    path: Path,
    *,
    action: str | None,
    module: str,
    personnel_field: str | None = None,
) -> int:
    if not path.is_file():
        return 0
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        act = str(obj.get("action", action or "event"))
        ts_raw = obj.get("ts_utc")
        ts = None
        if ts_raw:
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except ValueError:
                ts = None
        pid = str(obj.get(personnel_field, "")).strip() if personnel_field else None
        ctx = str(obj.get("sme_mois_context", "")).strip() or None
        payload = {
            k: v
            for k, v in obj.items()
            if k not in ("ts_utc", "action", "actor", personnel_field or "", "sme_mois_context")
        }
        repo.append_audit(
            action=act,
            actor=str(obj.get("edited_by") or obj.get("actor") or "") or None,
            module=module,
            personnel_id=pid or None,
            sme_mois_context=ctx,
            payload=payload or None,
            ts_utc=ts,
        )
        count += 1
    return count
