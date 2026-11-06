"""Journal central des documents ; miroir local optionnel après enregistrement."""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from sme_gend.services.search_text import normalize_search_text


ATTESTATION_HISTORY_DOC_TYPES: frozenset[str] = frozenset(
    {"attestation", "attestation_batch", "soit_transmis", "zip"}
)


def attestation_history_doc_types() -> frozenset[str]:
    return ATTESTATION_HISTORY_DOC_TYPES


def doc_entry_is_attestation_scope(entry: dict) -> bool:
    dtype = str(entry.get("doc_type") or "").strip()
    return dtype in ATTESTATION_HISTORY_DOC_TYPES


def _log_path() -> Path:
    from sme_gend.config.settings import project_root

    p = project_root() / "data" / "doc_gen_log.jsonl"
    return p


DOCUMENT_REVISION_KEY = "document_history_revision"


class DocumentJournalError(RuntimeError):
    """The output exists, but its central history could not be confirmed."""


def read_document_revision(*, settings=None) -> str:
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    _allowed_doc_types()
    try:
        return str((AppConfigRepository(settings if settings is not None else get_settings()).get_json(DOCUMENT_REVISION_KEY) or {}).get("token", ""))
    except Exception as exc:
        raise RuntimeError("Synchronisation du journal central indisponible.") from exc


def _publish_document_revision(settings) -> None:
    from uuid import uuid4
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    AppConfigRepository(settings).set_json(DOCUMENT_REVISION_KEY, {"token": uuid4().hex})


@contextmanager
def _document_write(settings):
    # One row serializes document imports and generators; no business-wide refresh.
    from sme_gend.repositories.mysql_connection import mysql_import_transaction, MySQLConnection
    with mysql_import_transaction(settings, read_committed=True):
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO app_config(config_key,payload) VALUES(%s,%s) "
                    "ON DUPLICATE KEY UPDATE config_key=VALUES(config_key)", (DOCUMENT_REVISION_KEY, "{}"))
        yield


def log_saved_list(destination, rows, *, doc_type, mois=None, settings=None, meta=None) -> None:
    """Called only after publication; preserve an explicit all-months scope."""
    from sme_gend.config import get_settings
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    settings = settings if settings is not None else get_settings()
    log_doc_generation(doc_type=doc_type, format="zip" if doc_type == "zip" else "xlsx",
        count=len(rows), dest_path=str(destination), mois=mois if mois is not None else resolve_export_mois(settings),
        meta=meta, settings=settings)


def _append_doc_gen_mysql(
    *,
    doc_type: str,
    format: str,
    persons: list[str] | None,
    dest_path: str | None,
    count: int,
    mois: str | None = None,
    meta: dict[str, Any] | None = None,
    mirror_entry: dict[str, Any] | None = None,
    settings=None,
) -> bool:
    try:
        from sme_gend.config import get_settings
        from sme_gend.repositories.app_config_repository import AppConfigRepository
        from sme_gend.services.auth_context import resolve_audit_actor

        payload: dict[str, Any] = {
            "doc_type": doc_type,
            "format": format,
            "count": count,
        }
        if persons:
            payload["persons"] = list(persons)
        if dest_path:
            payload["dest"] = str(dest_path)
        if (mois or "").strip():
            payload["mois"] = (mois or "").strip()
        if meta:
            payload["meta"] = meta
        from sme_gend.repositories.mysql_connection import current_transaction_settings
        settings = settings if settings is not None else (current_transaction_settings() or get_settings())
        repository = AppConfigRepository(settings)
        if not repository.table_exists("audit_journal"):
            return False
        with _document_write(settings):
            repository.append_audit(
                action="doc_gen",
                module="doc_gen",
                actor=mirror_entry.get("actor") if mirror_entry is not None else resolve_audit_actor(),
                ts_utc=datetime.fromisoformat(mirror_entry["ts_utc"]) if mirror_entry is not None else None,
                sme_mois_context=mois,
                payload=payload,
                **({"mirror": (_log_path(), [mirror_entry])} if mirror_entry is not None and _json_mirror_enabled() else {}),
            )
            _publish_document_revision(settings)
        return True
    except Exception as exc:
        from sme_gend.repositories.mysql_connection import CommittedMirrorError
        import logging
        if isinstance(exc, CommittedMirrorError):
            logging.getLogger(__name__).warning("Document journalisé ; copie locale en attente de reprise.")
            return True
        logging.getLogger(__name__).warning("Journal de document MySQL indisponible ; repli local utilisé si activé.")
        return False


def log_doc_generation(
    *,
    doc_type: str,
    format: str,
    persons: list[str] | None = None,
    dest_path: str | None = None,
    count: int = 1,
    mois: str | None = None,
    meta: dict[str, Any] | None = None,
    settings=None,
) -> None:
    """Enregistre une génération de document (base partagée + fichier local optionnel)."""
    from sme_gend.services.audit_log import audit_timestamp
    entry: dict[str, Any] = {
        "ts_utc": audit_timestamp().isoformat(),
        "doc_type": doc_type,
        "format": format,
        "count": count,
    }
    if persons:
        entry["persons"] = list(persons)
    if dest_path:
        entry["dest"] = str(dest_path)
    if (mois or "").strip():
        entry["mois"] = (mois or "").strip()
    if meta:
        entry["meta"] = meta
    from uuid import uuid4
    entry["_mirror_event_id"] = uuid4().hex
    from sme_gend.services.auth_context import resolve_audit_actor
    entry["actor"] = resolve_audit_actor() or ""
    persisted = _append_doc_gen_mysql(
        doc_type=doc_type,
        format=format,
        persons=persons,
        dest_path=dest_path,
        count=count,
        mois=mois,
        meta=meta,
        mirror_entry=entry,
        settings=settings,
    )
    if not persisted:
        _mirror_jsonl(entry)
        raise DocumentJournalError(
            "Le fichier a été généré, mais son enregistrement dans l’historique central n’a pas pu être confirmé. "
            "Conservez le fichier et vérifiez MySQL avant toute nouvelle génération."
        )


def _allowed_doc_types(type_key=""):
    from sme_gend.services.auth_guard import session_can_view_attestation_history, session_can_view_doc_log
    if not session_can_view_attestation_history():
        raise PermissionError("Accès refusé au journal des documents.")
    allowed = None if session_can_view_doc_log() else sorted(ATTESTATION_HISTORY_DOC_TYPES)
    if type_key:
        return [type_key] if allowed is None or type_key in allowed else []
    return allowed


def read_doc_log(max_entries: int = 500, *, type_key="", mois_filter="",
                 search_query="", type_labels=None, settings=None, offset=0) -> list[dict]:
    """Selected central database only; access scope and filters precede its limit."""
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    import re
    type_key = str(type_key or "").strip()
    mois_filter = str(mois_filter or "").strip()
    if mois_filter and not re.fullmatch(r"[0-9]{4}(?:-(?:0[1-9]|1[0-2]))?", mois_filter):
        raise ValueError("Le mois SME de recherche est invalide.")
    allowed = _allowed_doc_types(type_key)
    try:
        return AppConfigRepository(settings if settings is not None else get_settings()).read_doc_gen_tail(
            max_entries, doc_types=allowed, mois_filter=mois_filter,
            search_query=str(search_query or "").strip(), type_labels=type_labels, require_table=True,
            **({"offset": offset} if offset else {}))
    except Exception as exc:
        raise RuntimeError("Lecture du journal central impossible. Vérifiez MySQL puis réessayez.") from exc


def doc_log_months(*, settings=None) -> list[str]:
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    allowed = _allowed_doc_types()
    try:
        return AppConfigRepository(settings if settings is not None else get_settings()).list_doc_gen_months(doc_types=allowed)
    except Exception as exc:
        raise RuntimeError("Lecture des périodes du journal central impossible. Vérifiez MySQL.") from exc


def clear_doc_log() -> None:
    """Efface le journal local (MySQL conservé pour traçabilité)."""
    from sme_gend.services.auth_guard import session_can_purge_doc_log
    if not session_can_purge_doc_log():
        raise PermissionError("Accès refusé à l’effacement du journal local.")
    p = _log_path()
    if p.is_file():
        p.write_text("", encoding="utf-8")


def doc_entry_search_blob(entry: dict, *, type_labels: dict[str, str] | None = None) -> str:
    """Texte concaténé pour filtre recherche libre (insensible à la casse)."""
    labels = type_labels or {}
    parts: list[str] = []
    dtype = str(entry.get("doc_type") or "")
    if dtype:
        parts.append(dtype)
        label = labels.get(dtype)
        if label:
            parts.append(label)
            if entry.get("format"):
                parts.append(f"{label} ({str(entry['format']).upper()})")
    try:
        stamp = datetime.fromisoformat(str(entry.get("ts_utc") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        parts.append(stamp.astimezone().strftime("%d/%m/%Y %H:%M:%S"))
    except (ValueError, TypeError, OverflowError, OSError):
        pass
    for key in ("format", "mois", "dest", "actor"):
        val = entry.get(key)
        if val:
            parts.append(str(val))
    persons = entry.get("persons")
    if isinstance(persons, list):
        parts.extend(str(p) for p in persons if p)
    elif persons:
        parts.append(str(persons))
    meta = entry.get("meta")
    if isinstance(meta, dict):
        for k, v in meta.items():
            if v is not None:
                parts.append(f"{k}={v}")
    count = entry.get("count")
    if count is not None:
        parts.append(str(count))
    return normalize_search_text(" ".join(parts))


def doc_entry_passes_filters(
    entry: dict,
    *,
    type_key: str = "",
    mois_filter: str = "",
    search_query: str = "",
    type_labels: dict[str, str] | None = None,
) -> bool:
    """Filtre type, mois SME (sous-chaîne) et recherche libre."""
    if type_key and entry.get("doc_type") != type_key:
        return False
    mois_filter = (mois_filter or "").strip()
    if mois_filter:
        entry_mois = (entry.get("mois") or "").strip()
        if not entry_mois.startswith(mois_filter):
            return False
    q = normalize_search_text(search_query)
    if q and q not in doc_entry_search_blob(entry, type_labels=type_labels):
        return False
    return True


DOC_LOG_CSV_FIELDS: tuple[str, ...] = (
    "ts_utc",
    "doc_type",
    "format",
    "mois",
    "count",
    "persons",
    "dest",
    "actor",
    "meta",
    "csv_encoding",
)

_PERSONS_SEP = " | "


@dataclass(frozen=True, slots=True)
class DocLogMergeResult:
    """Résultat d'un import CSV : ajouts seulement, jamais de suppression."""

    added: int
    already_present: int
    invalid: int


def _json_mirror_enabled() -> bool:
    return os.environ.get("SME_AUDIT_JSON_MIRROR", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _mirror_jsonl(entry: dict[str, Any]) -> None:
    if not _json_mirror_enabled():
        return
    _append_jsonl_entry(entry)


def _append_jsonl_entry(entry: dict[str, Any]) -> None:
    """Ajoute une ligne au journal local. N'efface jamais le fichier."""
    from sme_gend.services.local_audit_mirror import append_json_mirror
    try:
        append_json_mirror(_log_path(), [entry])
    except OSError:
        import logging
        logging.getLogger(__name__).warning("Copie locale du journal de documents inaccessible.")


def _normalize_ts(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        dt = None
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    dt = dt.replace(microsecond=(dt.microsecond // 1000) * 1000)
    return dt.isoformat(timespec="milliseconds" if dt.microsecond else "seconds")


def _persons_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(p).strip() for p in raw if str(p).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    if _PERSONS_SEP in text:
        return [p.strip() for p in text.split(_PERSONS_SEP) if p.strip()]
    return [text]


def _meta_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"detail": text}
        return parsed if isinstance(parsed, dict) else {"detail": text}
    return {"detail": text}


def _entry_count(entry: dict[str, Any], persons: list[str]) -> int:
    raw = entry.get("count")
    if raw is None or str(raw).strip() == "":
        return len(persons) or 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return len(persons) or 1


def doc_entry_fingerprint(entry: dict[str, Any]) -> str:
    """Identité stable d'une trace (réimport du même CSV = déjà présente)."""
    persons = _persons_list(entry.get("persons"))
    payload = {
        "ts": _normalize_ts(entry.get("ts_utc")),
        "doc_type": str(entry.get("doc_type") or "").strip(),
        "format": str(entry.get("format") or "").strip().lower(),
        "mois": str(entry.get("mois") or "").strip(),
        "count": _entry_count(entry, persons),
        "persons": persons,
        "dest": str(entry.get("dest") or "").strip(),
        "actor": str(entry.get("actor") or "").strip(),
        "meta": _meta_dict(entry.get("meta")),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def plan_doc_log_merge(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Retourne les traces à ajouter et le nombre déjà présentes. Rien n'est retiré."""
    seen = {doc_entry_fingerprint(entry) for entry in existing}
    to_add: list[dict[str, Any]] = []
    skipped = 0
    for entry in incoming:
        fp = doc_entry_fingerprint(entry)
        if fp in seen:
            skipped += 1
            continue
        seen.add(fp)
        to_add.append(entry)
    to_add.sort(key=lambda e: _normalize_ts(e.get("ts_utc")))
    return to_add, skipped


def export_doc_log_csv(entries: list[dict[str, Any]]) -> str:
    """CSV point-virgule, réimportable, des entrées fournies (souvent le filtre affiché)."""
    from sme_gend.services.csv_safety import CSV_TEXT_ENCODING, spreadsheet_cell
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=list(DOC_LOG_CSV_FIELDS),
        delimiter=";",
        lineterminator="\n",
        extrasaction="ignore",
    )
    writer.writeheader()
    for entry in entries:
        persons = _persons_list(entry.get("persons"))
        meta = _meta_dict(entry.get("meta"))
        values = {
                "ts_utc": entry.get("ts_utc") or "",
                "doc_type": entry.get("doc_type") or "",
                "format": entry.get("format") or "",
                "mois": entry.get("mois") or "",
                "count": _entry_count(entry, persons),
                "persons": _PERSONS_SEP.join(persons),
                "dest": entry.get("dest") or "",
                "actor": entry.get("actor") or "",
                "meta": json.dumps(meta, ensure_ascii=False, sort_keys=True, default=str)
                if meta
                else "",
                "csv_encoding": CSV_TEXT_ENCODING,
            }
        writer.writerow({key: spreadsheet_cell(value) for key, value in values.items()})
    return buf.getvalue()


def _csv_delimiter(header_line: str) -> str:
    if header_line.count(";") >= header_line.count(","):
        return ";"
    return ","


def parse_doc_log_csv(text: str) -> tuple[list[dict[str, Any]], int]:
    """Lit un CSV d'historique. Retourne (entrées valides, lignes ignorées)."""
    raw = (text or "").lstrip("\ufeff")
    if not raw.strip():
        return [], 0
    first = raw.splitlines()[0]
    reader = csv.DictReader(io.StringIO(raw), delimiter=_csv_delimiter(first))
    if reader.fieldnames:
        reader.fieldnames = [
            (name or "").strip().lstrip("\ufeff") for name in reader.fieldnames
        ]
    if not reader.fieldnames or "doc_type" not in reader.fieldnames or "ts_utc" not in reader.fieldnames:
        raise ValueError(
            "Fichier CSV invalide : colonnes ts_utc et doc_type attendues."
        )
    entries: list[dict[str, Any]] = []
    invalid = 0
    for row in reader:
        if row is None:
            continue
        from sme_gend.services.csv_safety import CSV_TEXT_ENCODING, decode_spreadsheet_cell
        if row.get("csv_encoding") == CSV_TEXT_ENCODING:
            row = {key: decode_spreadsheet_cell(value) for key, value in row.items()}
        if not any(str(v or "").strip() for v in row.values()):
            continue
        ts = _normalize_ts(row.get("ts_utc"))
        doc_type = str(row.get("doc_type") or "").strip()
        if not ts or not doc_type:
            invalid += 1
            continue
        persons = _persons_list(row.get("persons"))
        count_raw = row.get("count")
        entry: dict[str, Any] = {
            "ts_utc": ts,
            "doc_type": doc_type,
            "format": str(row.get("format") or "").strip(),
            "mois": str(row.get("mois") or "").strip(),
            "count": _entry_count({"count": count_raw}, persons),
            "persons": persons,
            "dest": str(row.get("dest") or "").strip(),
            "actor": str(row.get("actor") or "").strip(),
            "meta": _meta_dict(row.get("meta")),
        }
        entries.append(entry)
    return entries, invalid


def _read_jsonl_entries() -> list[dict[str, Any]]:
    p = _log_path()
    if not p.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                entries.append(item)
    except OSError:
        return entries
    return entries


def _load_existing_doc_entries() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        from sme_gend.config import get_settings
        from sme_gend.repositories.app_config_repository import AppConfigRepository

        rows.extend(AppConfigRepository(get_settings()).read_doc_gen_tail(50000))
    except Exception:
        pass
    rows.extend(_read_jsonl_entries())
    return rows


def _audit_table_ready() -> bool:
    try:
        from sme_gend.config import get_settings
        from sme_gend.repositories.app_config_repository import AppConfigRepository

        return AppConfigRepository(get_settings()).table_exists("audit_journal")
    except Exception:
        return False


def _append_imported_mysql(entry: dict[str, Any], *, settings=None) -> None:
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    ts = datetime.fromisoformat(_normalize_ts(entry.get("ts_utc"))).replace(tzinfo=timezone.utc)
    persons = _persons_list(entry.get("persons"))
    meta = _meta_dict(entry.get("meta"))
    payload: dict[str, Any] = {
        "doc_type": entry.get("doc_type") or "",
        "format": entry.get("format") or "",
        "count": _entry_count(entry, persons),
    }
    if persons:
        payload["persons"] = persons
    dest = str(entry.get("dest") or "").strip()
    if dest:
        payload["dest"] = dest
    mois = str(entry.get("mois") or "").strip()
    if mois:
        payload["mois"] = mois
    if meta:
        payload["meta"] = meta
    from uuid import uuid4
    entry.setdefault("_mirror_event_id", uuid4().hex)
    from sme_gend.repositories.mysql_connection import current_transaction_settings
    AppConfigRepository(settings if settings is not None else (current_transaction_settings() or get_settings())).append_audit(
        action="doc_gen",
        module="doc_gen",
        actor=str(entry.get("actor") or "").strip() or None,
        payload=payload,
        ts_utc=ts,
        **({"mirror": (_log_path(), [entry])} if _json_mirror_enabled() else {}),
    )


def merge_doc_log_from_csv(path: Path, *, settings=None) -> DocLogMergeResult:
    """Merge only into this central database, atomically and without a tail limit."""
    from sme_gend.services.auth_guard import session_can_view_doc_log
    if not session_can_view_doc_log():
        raise PermissionError("Accès refusé à l’import de l’historique des documents.")
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    settings = settings if settings is not None else get_settings()
    incoming, invalid = parse_doc_log_csv(Path(path).read_text(encoding="utf-8-sig"))
    repository = AppConfigRepository(settings)
    if not repository.table_exists("audit_journal"):
        raise RuntimeError("Import impossible : le journal central est absent. Aucune trace ajoutée.")
    to_add, skipped = [], 0
    try:
        with _document_write(settings):
            existing = repository.read_doc_gen_candidates([_normalize_ts(row["ts_utc"]).split(".")[0] for row in incoming])
            to_add, skipped = plan_doc_log_merge(existing, incoming)
            for entry in to_add:
                _append_imported_mysql(entry, settings=settings)
            from sme_gend.services.auth_context import resolve_audit_actor
            repository.append_audit(action="document_history_import", module="document_history",
                actor=resolve_audit_actor(), payload={"added": len(to_add), "already_present": skipped, "invalid": invalid})
            if to_add:
                _publish_document_revision(settings)
    except CommittedMirrorError:
        import logging
        logging.getLogger(__name__).warning("Historique importé ; copie locale en attente de reprise.")
    except Exception as exc:
        raise RuntimeError("Import de l’historique interrompu. Vérifiez le journal central avant de réessayer.") from exc
    return DocLogMergeResult(added=len(to_add), already_present=skipped, invalid=invalid)
