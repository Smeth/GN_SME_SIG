"""Traçabilité des modifications sur le personnel (fichier JSON Lines, rotation mensuelle)."""

from __future__ import annotations

import json
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import liste_layout_config_path
from sme_gend.models.liste_generale import ListeGeneraleRow


def _audit_dir() -> Path:
    return liste_layout_config_path().parent


def _audit_file_path(ts_utc: str | None = None) -> Path:
    """Chemin du fichier JSONL pour le mois donné (défaut : mois courant UTC)."""
    if ts_utc:
        try:
            dt = datetime.fromisoformat(ts_utc)
            ym = dt.strftime("%Y-%m")
        except ValueError:
            ym = datetime.now(timezone.utc).strftime("%Y-%m")
    else:
        ym = datetime.now(timezone.utc).strftime("%Y-%m")
    return _audit_dir() / f"personnel_audit_{ym}.jsonl"


def _all_audit_files() -> list[Path]:
    """Tous les fichiers JSONL d’audit présents, triés chronologiquement."""
    d = _audit_dir()
    files = sorted(d.glob("personnel_audit_*.jsonl"))
    legacy = d / "personnel_audit.jsonl"
    if legacy.is_file():
        files = [legacy] + [f for f in files if f != legacy]
    return files


def personnel_audit_file_path() -> Path:
    """Chemin du fichier JSONL d’audit pour le mois courant."""
    return _audit_file_path()


def _serialize(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def diff_personnel_rows(old: ListeGeneraleRow, new: ListeGeneraleRow) -> dict[str, dict[str, Any]]:
    """Champs modifiés avec anciennes / nouvelles valeurs."""
    changes: dict[str, dict[str, Any]] = {}
    for f in fields(ListeGeneraleRow):
        name = f.name
        o = getattr(old, name)
        n = getattr(new, name)
        if o != n:
            changes[name] = {"old": _serialize(o), "new": _serialize(n)}
    return changes


def append_audit_event(
    *,
    action: str,
    sme_mois_context: str | None = None,
    **extra: Any,
) -> None:
    """
    Événement générique dans le fichier JSONL mensuel courant
    (ex. import Access → SQLite). Les clés ``extra`` sont ajoutées au même niveau que ``action``.
    """
    ts = datetime.now(timezone.utc).isoformat()
    path = _audit_file_path(ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "ts_utc": ts,
        "action": action,
        **extra,
    }
    if sme_mois_context:
        entry["sme_mois_context"] = sme_mois_context
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_personnel_audit(
    *,
    action: str,
    personnel_id: str,
    changes: dict[str, dict[str, Any]],
    snapshot_before: dict[str, Any] | None = None,
    snapshot_after: dict[str, Any] | None = None,
    sme_mois_context: str | None = None,
    edited_by: str | None = None,
    edit_motif: str | None = None,
    ts_utc: str | None = None,
) -> None:
    """Ajoute une ligne d'audit (append-only). ts_utc permet de forcer un horodatage (import rétroactif)."""
    effective_ts = ts_utc or datetime.now(timezone.utc).isoformat()
    path = _audit_file_path(effective_ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "ts_utc": effective_ts,
        "action": action,
        "personnel_id": personnel_id,
        "changes": changes,
    }
    if sme_mois_context:
        entry["sme_mois_context"] = sme_mois_context
    eb = (edited_by or "").strip()
    if eb:
        entry["edited_by"] = eb
    em = (edit_motif or "").strip()
    if em:
        entry["edit_motif"] = em
    if snapshot_before is not None:
        entry["before"] = snapshot_before
    if snapshot_after is not None:
        entry["after"] = snapshot_after
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def row_to_audit_dict(row: ListeGeneraleRow) -> dict[str, Any]:
    """Snapshot sérialisable pour l'audit."""
    if not is_dataclass(row):
        return {}
    d = asdict(row)
    for k, v in list(d.items()):
        d[k] = _serialize(v)
    return d
