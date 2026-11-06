"""Traçabilité des modifications — MySQL ``audit_journal`` (miroir JSONL optionnel)."""

from __future__ import annotations

from sme_gend.repositories.mysql_connection import CommittedMirrorError

import json
import logging
import os
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sme_gend.config.settings import liste_layout_config_path
from sme_gend.models.liste_generale import ListeGeneraleRow

_LOG = logging.getLogger(__name__)


def _json_mirror_enabled() -> bool:
    """Miroir local JSONL (désactivé par défaut — données dans MySQL)."""
    return os.environ.get("SME_AUDIT_JSON_MIRROR", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _audit_dir() -> Path:
    return liste_layout_config_path().parent


def audit_timestamp(value=None) -> datetime:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Horodatage d’audit invalide ; aucune trace enregistrée.") from exc
    if not isinstance(value, datetime):
        raise ValueError("Horodatage d’audit invalide ; aucune trace enregistrée.")
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def _audit_file_path(ts_utc: str | None = None) -> Path:
    """Chemin du fichier JSONL pour le mois donné (défaut : mois courant UTC)."""
    if ts_utc:
        try:
            dt = audit_timestamp(ts_utc)
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


def _user_activity_path() -> Path:
    return _audit_dir() / "user_activity.jsonl"


def append_user_activity(
    *,
    action: str,
    module: str = "",
    detail: str = "",
    actor: str | None = None,
    **extra: Any,
) -> None:
    """
    Journal applicatif « qui a fait quoi » (indépendant de l'auth activée ou non).
    Fichier : ``data/user_activity.jsonl``.
    """
    from sme_gend.services.auth_context import resolve_audit_actor

    ts = audit_timestamp().isoformat()
    path = _user_activity_path()
    entry: dict[str, Any] = {
        **extra,
        "ts_utc": ts,
        "action": action,
        "actor": (actor or "").strip() or resolve_audit_actor(),
    }
    mod = (module or "").strip()
    if mod:
        entry["module"] = mod
    det = (detail or "").strip()
    if det:
        entry["detail"] = det
    _append_audit_mysql(
        action=action,
        ts_utc=audit_timestamp(ts),
        actor=entry.get("actor"),
        module=module or None,
        payload={k: v for k, v in entry.items() if k not in ("ts_utc", "action", "actor", "module")},
        mirror=(path, [entry]) if _json_mirror_enabled() else None,
    )


def _append_audit_mysql(
    *,
    action: str,
    actor: str | None = None,
    module: str | None = None,
    personnel_id: str | None = None,
    sme_mois_context: str | None = None,
    payload: dict[str, Any] | None = None,
    mirror: tuple | None = None,
    ts_utc=None,
) -> None:
    timestamp = audit_timestamp(ts_utc)
    try:
        from sme_gend.config import get_settings
        from sme_gend.config.sme_context import mois_actif_courant
        from sme_gend.repositories.app_config_repository import AppConfigRepository
        from sme_gend.repositories.mysql_connection import current_transaction_settings

        repo = AppConfigRepository(current_transaction_settings() or get_settings())
        if not repo.table_exists("audit_journal"):
            raise RuntimeError("Journal d’audit absent : trace impossible.")
        selected = current_transaction_settings()
        ctx_mois = (sme_mois_context or (selected.mysql_view_mois if selected else None) or mois_actif_courant() or "").strip() or None
        repo.append_audit(
            action=action,
            actor=actor,
            module=module,
            personnel_id=personnel_id,
            sme_mois_context=ctx_mois[:7] if ctx_mois and len(ctx_mois) >= 7 else ctx_mois,
            payload=payload,
            ts_utc=timestamp,
            **({"mirror": mirror} if mirror is not None else {}),
        )
    except CommittedMirrorError:
        raise
    except Exception:
        _LOG.error("Échec d'écriture dans audit_journal ; opération non confirmée.")
        from sme_gend.repositories.mysql_connection import current_transaction_settings
        if current_transaction_settings() is not None:
            raise RuntimeError("Modification annulée : journalisation MySQL impossible.")
        raise RuntimeError("Journalisation MySQL impossible ; vérifiez le journal avant de recommencer.")


def read_user_activity_tail(max_lines: int = 200) -> list[dict[str, Any]]:
    """Dernières lignes du journal d'activité (MySQL centralisé, module auth)."""
    return search_auth_activity(limit=max_lines)


def search_auth_activity(
    *,
    action: str | None = None,
    actor: str | None = None,
    text: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 500,
    settings=None,
) -> list[dict[str, Any]]:
    """Read the selected central journal, applying filters before its limit."""
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository, audit_date_bounds
    from sme_gend.services.auth_guard import session_can_view_auth_journal
    if not session_can_view_auth_journal():
        raise PermissionError("Accès refusé au journal d’authentification.")
    audit_date_bounds(date_from, date_to)
    try:
        rows = AppConfigRepository(settings if settings is not None else get_settings()).search_audit_journal(
            module="auth", action=action or None, actor=actor or None, text=text or None,
            date_from=date_from, date_to=date_to, limit=limit, require_table=True,
        )
        return list(reversed(rows))
    except Exception as exc:
        raise RuntimeError("Lecture du journal central impossible. Vérifiez MySQL puis réessayez.") from exc


def export_auth_activity_csv(lines: list[dict[str, Any]]) -> str:
    """Export CSV (;) des lignes du journal auth."""
    import csv
    import io
    from sme_gend.services.csv_safety import spreadsheet_cell

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["Date UTC", "Acteur", "Action", "Module", "Détail"])
    for row in lines:
        detail = str(row.get("detail") or "")
        if not detail and isinstance(row.get("payload"), dict):
            detail = str(row["payload"].get("detail") or "")
        writer.writerow(
            [spreadsheet_cell(value) for value in [
                row.get("ts_utc") or "",
                row.get("actor") or "",
                row.get("action") or "",
                row.get("module") or "auth",
                detail,
            ]]
        )
    return buf.getvalue()


def _read_user_activity_local_fallback(max_lines: int = 200) -> list[dict[str, Any]]:
    """Repli fichier local si MySQL indisponible."""
    path = _user_activity_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, max_lines) :]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def append_audit_event(
    *,
    action: str,
    sme_mois_context: str | None = None,
    actor: str | None = None,
    **extra: Any,
) -> None:
    """
    Événement générique dans le fichier JSONL mensuel courant
    (ex. import Access → MySQL). Les clés ``extra`` sont ajoutées au même niveau que ``action``.
    """
    from sme_gend.services.auth_context import resolve_audit_actor

    if not sme_mois_context:
        legacy_month = extra.get("sme_mois")
        import re
        if isinstance(legacy_month, str) and re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", legacy_month):
            sme_mois_context = legacy_month
    ts = audit_timestamp().isoformat()
    path = _audit_file_path(ts)
    entry: dict[str, Any] = {
        **extra,
        "ts_utc": ts,
        "action": action,
        "actor": (actor or "").strip() or resolve_audit_actor(),
    }
    if sme_mois_context:
        entry["sme_mois_context"] = sme_mois_context
    _append_audit_mysql(
        action=action,
        ts_utc=audit_timestamp(ts),
        actor=entry.get("actor"),
        module="system",
        sme_mois_context=sme_mois_context,
        payload={k: v for k, v in entry.items() if k not in ("ts_utc", "action", "actor")},
        mirror=(path, [entry]) if _json_mirror_enabled() else None,
    )


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
    from sme_gend.services.auth_context import resolve_audit_actor

    timestamp = audit_timestamp(ts_utc)
    effective_ts = timestamp.isoformat()
    path = _audit_file_path(effective_ts)
    entry: dict[str, Any] = {
        "ts_utc": effective_ts,
        "action": action,
        "personnel_id": personnel_id,
        "changes": changes,
    }
    if sme_mois_context:
        entry["sme_mois_context"] = sme_mois_context
    from sme_gend.services.auth_context import resolve_write_actor
    eb = resolve_write_actor(edited_by)
    if eb:
        entry["edited_by"] = eb
    em = (edit_motif or "").strip()
    if em:
        entry["edit_motif"] = em
    if snapshot_before is not None:
        entry["before"] = snapshot_before
    if snapshot_after is not None:
        entry["after"] = snapshot_after
    _append_audit_mysql(
        action=action,
        actor=eb,
        module="personnel",
        ts_utc=timestamp,
        personnel_id=personnel_id,
        sme_mois_context=sme_mois_context,
        payload={
            "changes": changes,
            "edit_motif": em or None,
            "before": snapshot_before,
            "after": snapshot_after,
        },
        mirror=(path, [entry]) if _json_mirror_enabled() else None,
    )


def _persist_personnel_audit_batch_mysql(db_entries: list[dict[str, Any]], *, mirror: tuple | None = None) -> None:
    """Lot MySQL ; repli ligne à ligne si ``append_audit_batch`` absent (anciennes bases)."""
    if not db_entries:
        return
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    from sme_gend.repositories.mysql_connection import current_transaction_settings
    repo = AppConfigRepository(current_transaction_settings() or get_settings())
    batch_fn = getattr(repo, "append_audit_batch", None)
    if callable(batch_fn):
        batch_fn(db_entries, **({"mirror": mirror} if mirror is not None else {}))
        return
    for index, item in enumerate(db_entries):
        repo.append_audit(
            action=str(item.get("action") or "update"),
            actor=item.get("actor"),
            module=item.get("module"),
            personnel_id=item.get("personnel_id"),
            sme_mois_context=item.get("sme_mois_context"),
            payload=item.get("payload"),
            ts_utc=item.get("ts_utc"),
            **({"mirror": (mirror[0], [mirror[1][index]])} if mirror is not None else {}),
        )


def append_personnel_audit_batch(entries: list[dict[str, Any]]) -> None:
    """Écrit l'audit d'un import par lot, sans ouvrir une connexion par agent."""
    if not entries:
        return
    from sme_gend.services.auth_context import resolve_audit_actor

    ts = audit_timestamp()
    ts_iso = ts.isoformat()
    actor = resolve_audit_actor()
    month_context = next(
        (str(entry.get("sme_mois_context") or "").strip() for entry in entries
         if str(entry.get("sme_mois_context") or "").strip()),
        None,
    )
    db_entries: list[dict[str, Any]] = []
    mirror_entries: list[dict[str, Any]] = []
    for item in entries:
        from sme_gend.services.auth_context import resolve_write_actor
        effective_actor = resolve_write_actor(str(item.get("edited_by") or "").strip() or actor)
        motif = (str(item.get("edit_motif") or "").strip() or None)
        personnel_id = str(item.get("personnel_id") or "").strip()
        changes = item.get("changes") or {}
        snapshot_before = item.get("snapshot_before")
        snapshot_after = item.get("snapshot_after")
        context = str(item.get("sme_mois_context") or "").strip() or None
        db_entries.append(
            {
                "ts_utc": ts,
                "action": str(item.get("action") or "update"),
                "actor": effective_actor,
                "module": "personnel",
                "personnel_id": personnel_id,
                "sme_mois_context": context,
                "payload": {
                    "changes": changes,
                    "edit_motif": motif,
                    "before": snapshot_before,
                    "after": snapshot_after,
                },
            }
        )
        mirror_entry: dict[str, Any] = {
            "ts_utc": ts_iso,
            "action": str(item.get("action") or "update"),
            "personnel_id": personnel_id,
            "changes": changes,
        }
        if context:
            mirror_entry["sme_mois_context"] = context
        if effective_actor:
            mirror_entry["edited_by"] = effective_actor
        if motif:
            mirror_entry["edit_motif"] = motif
        if snapshot_before is not None:
            mirror_entry["before"] = snapshot_before
        if snapshot_after is not None:
            mirror_entry["after"] = snapshot_after
        mirror_entries.append(mirror_entry)

    try:
        mirror = (_audit_file_path(ts_iso), mirror_entries) if _json_mirror_enabled() else None
        _persist_personnel_audit_batch_mysql(db_entries, mirror=mirror)
    except CommittedMirrorError:
        raise
    except Exception:
        _LOG.error("Échec d’écriture du lot d’audits d’import ; aucune confirmation de journalisation.")
        from sme_gend.repositories.mysql_connection import current_transaction_settings
        if current_transaction_settings() is not None:
            raise RuntimeError("Importation annulée : écriture du journal d'audit impossible.")
        raise RuntimeError("Écriture du journal d’import impossible ; vérifiez son état avant de recommencer.")


def row_to_audit_dict(row: ListeGeneraleRow) -> dict[str, Any]:
    """Snapshot sérialisable pour l'audit."""
    if not is_dataclass(row):
        return {}
    d = asdict(row)
    for k, v in list(d.items()):
        d[k] = _serialize(v)
    return d


def _parse_audit_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def row_from_audit_dict(data: dict[str, Any] | None) -> ListeGeneraleRow | None:
    """Reconstruit une ligne liste générale depuis un snapshot d'audit."""
    if not isinstance(data, dict) or not data:
        return None
    field_names = {f.name for f in fields(ListeGeneraleRow)}
    kwargs: dict[str, Any] = {}
    for name in field_names:
        raw = data.get(name)
        if name in ("date_naissance", "date_grade"):
            kwargs[name] = _parse_audit_datetime(raw)
        elif name in ("ordre_tri", "presseance"):
            if raw is None or raw == "":
                kwargs[name] = None
            else:
                try:
                    kwargs[name] = int(raw)
                except (TypeError, ValueError):
                    kwargs[name] = None
        elif name == "id":
            kwargs[name] = str(raw or "").strip() or "0"
        else:
            kwargs[name] = None if raw is None else str(raw).strip() or None
    return ListeGeneraleRow(**kwargs)
