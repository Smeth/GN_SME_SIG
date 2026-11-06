"""Journal dédié aux actions sensibles (admin / super_admin) — MySQL ``audit_journal``."""

from __future__ import annotations

from sme_gend.repositories.mysql_connection import CommittedMirrorError

import json
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
import logging
import re
import unicodedata

from sme_gend.models.liste_generale import ListeGeneraleRow

SENSITIVE_AUDIT_MODULE = "sensitive_data"

ACTION_PERSONNEL_SENSITIVE = "personnel_sensitive_update"
ACTION_PERSONNEL_AGE_UPDATE = "personnel_age_update"
ACTION_DOSSIER_DIPLOME_ADD = "dossier_diplome_add"
ACTION_DOSSIER_DIPLOME_UPDATE = "dossier_diplome_update"
ACTION_DOSSIER_DIPLOME_DELETE = "dossier_diplome_delete"
ACTION_DOSSIER_PASSATION_ADD = "dossier_passation_add"
ACTION_DOSSIER_PASSATION_UPDATE = "dossier_passation_update"
ACTION_DOSSIER_PASSATION_DELETE = "dossier_passation_delete"
ACTION_DOSSIER_AFFECTATION_ADD = "dossier_affectation_add"
ACTION_DOSSIER_AFFECTATION_UPDATE = "dossier_affectation_update"
ACTION_DOSSIER_AFFECTATION_DELETE = "dossier_affectation_delete"

ACTION_LABELS_FR: dict[str, str] = {
    **{"dossier_"+family+"_"+operation:label+" — "+verb
        for family,label in (("mission","Mission"),("stage","Stage"),("malade","Maladie"),("desertion","Désertion"),("conge","Congé"),("distinction","Distinction"))
        for operation,verb in (("add","ajout"),("update","modification"),("delete","suppression"))},
    ACTION_PERSONNEL_SENSITIVE: "Fiche — champs sensibles modifiés",
    ACTION_PERSONNEL_AGE_UPDATE: "Fiche — date de naissance / âge modifié(e)",
    ACTION_DOSSIER_DIPLOME_ADD: "Diplôme — ajout",
    ACTION_DOSSIER_DIPLOME_UPDATE: "Diplôme — modification",
    ACTION_DOSSIER_DIPLOME_DELETE: "Diplôme — suppression",
    ACTION_DOSSIER_PASSATION_ADD: "Passation de grade — ajout",
    ACTION_DOSSIER_PASSATION_UPDATE: "Passation de grade — modification",
    ACTION_DOSSIER_PASSATION_DELETE: "Passation de grade — suppression",
    ACTION_DOSSIER_AFFECTATION_ADD: "Affectation — ajout",
    ACTION_DOSSIER_AFFECTATION_UPDATE: "Affectation — modification",
    ACTION_DOSSIER_AFFECTATION_DELETE: "Affectation — suppression",
}


def _json_mirror_enabled() -> bool:
    return os.environ.get("SME_SENSITIVE_AUDIT_JSON_MIRROR", "1").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _local_log_path() -> Path:
    from sme_gend.config.settings import project_root

    p = project_root() / "data" / "sensitive_audit_log.jsonl"
    return p


def _append_entry(
    *,
    action: str,
    personnel_id: str | None = None,
    personnel_nom: str | None = None,
    sme_mois_context: str | None = None,
    detail: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    from sme_gend.config import get_settings
    from sme_gend.config.sme_context import mois_actif_courant
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.repositories.mysql_connection import current_transaction_settings

    actor = resolve_audit_actor()
    transaction_settings = current_transaction_settings()
    selected_mois = transaction_settings.mysql_view_mois if transaction_settings else None
    ctx = (sme_mois_context or selected_mois or mois_actif_courant() or "").strip() or None
    body: dict[str, Any] = dict(payload or {})
    if personnel_nom:
        body["personnel_nom"] = personnel_nom
    if detail:
        body["detail"] = detail

    from sme_gend.services.audit_log import audit_timestamp
    timestamp = audit_timestamp()
    ts = timestamp.isoformat()
    entry = {
        **body,
        "ts_utc": ts,
        "action": action,
        "actor": actor,
        "module": SENSITIVE_AUDIT_MODULE,
        "personnel_id": (personnel_id or "").strip() or None,
        "sme_mois_context": ctx,
    }
    try:
        AppConfigRepository(transaction_settings or get_settings()).append_audit(
            action=action,
            module=SENSITIVE_AUDIT_MODULE,
            actor=actor,
            personnel_id=(personnel_id or "").strip() or None,
            sme_mois_context=ctx[:7] if ctx and len(ctx) >= 7 else ctx,
            payload=body,
            ts_utc=timestamp,
            **({"mirror": (_local_log_path(), [entry])} if _json_mirror_enabled() else {}),
        )
    except CommittedMirrorError:
        raise
    except Exception as exc:
        logging.getLogger(__name__).error("Écriture du journal sensible impossible.")
        if current_transaction_settings() is not None:
            raise RuntimeError("Modification annulée : journal sensible indisponible.") from exc
        raise RuntimeError("Journal sensible indisponible ; aucune trace confirmée.") from exc



def log_personnel_sensitive_update(
    before: ListeGeneraleRow,
    after: ListeGeneraleRow,
    changes: dict[str, dict[str, Any]],
) -> None:
    from sme_gend.services.sensitive_data_policy import SENSITIVE_FIELD_LABELS_FR

    labels = [
        SENSITIVE_FIELD_LABELS_FR.get(k, k)
        for k in sorted(changes.keys())
    ]
    _append_entry(
        action=ACTION_PERSONNEL_SENSITIVE,
        personnel_id=str(after.id or ""),
        personnel_nom=(after.noms or "").strip() or None,
        detail=" ; ".join(labels),
        payload={"changes": changes},
    )


def log_personnel_age_update(
    before: ListeGeneraleRow,
    after: ListeGeneraleRow,
    change: dict[str, Any],
) -> None:
    """Trace dédiée obligatoire pour toute modification de date de naissance."""
    from sme_gend.services.audit_log import _serialize

    old_v = _serialize(change.get("old"))
    new_v = _serialize(change.get("new"))
    detail = f"Date de naissance : {old_v} → {new_v}"
    _append_entry(
        action=ACTION_PERSONNEL_AGE_UPDATE,
        personnel_id=str(after.id or ""),
        personnel_nom=(after.noms or "").strip() or None,
        detail=detail,
        payload={
            "field": "date_naissance",
            "changes": {"date_naissance": change},
            "before": _serialize(before.date_naissance),
            "after": _serialize(after.date_naissance),
        },
    )


def log_dossier_history_action(
    *,
    action: str,
    row: ListeGeneraleRow,
    detail: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = dict(extra or {})
    payload["detail"] = detail
    _append_entry(
        action=action,
        personnel_id=str(row.id or ""),
        personnel_nom=(row.noms or "").strip() or None,
        detail=detail,
        payload=payload,
    )


@dataclass(slots=True)
class SensitiveAuditQuery:
    action: str = ""
    actor: str = ""
    personnel_id: str = ""
    personnel_nom: str = ""
    sme_mois: str = ""
    date_from: date | None = None
    date_to: date | None = None
    text: str = ""
    limit: int = 500


def _parse_local_entries(max_entries: int) -> list[dict[str, Any]]:
    path = _local_log_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, max_entries) :]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("module") == SENSITIVE_AUDIT_MODULE:
            out.append(obj)
    return out


def validate_sensitive_audit_query(query: SensitiveAuditQuery) -> SensitiveAuditQuery:
    from sme_gend.repositories.app_config_repository import audit_date_bounds

    clean = replace(query, **{name: str(getattr(query, name) or "").strip()
        for name in ("action", "actor", "personnel_id", "personnel_nom", "sme_mois", "text")})
    audit_date_bounds(clean.date_from, clean.date_to)
    if clean.sme_mois:
        if not re.fullmatch(r"[0-9]{4}(?:-(?:0[1-9]|1[0-2]))?", clean.sme_mois) or clean.sme_mois[:4] == "0000":
            raise ValueError("L'année ou le mois SME de recherche est invalide.")
    try:
        clean.limit = max(1, min(int(clean.limit), 2000))
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("La limite de recherche est invalide.") from exc
    return clean


def search_sensitive_audit(query: SensitiveAuditQuery, *, settings=None) -> list[dict[str, Any]]:
    """The selected central database is authoritative, even when it has no matches."""
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_guard import session_can_view_sensitive_audit

    if not session_can_view_sensitive_audit():
        raise PermissionError("Accès refusé au journal des données sensibles.")
    query = validate_sensitive_audit_query(query)
    try:
        return AppConfigRepository(settings if settings is not None else get_settings()).search_audit_journal(
            module=SENSITIVE_AUDIT_MODULE, action=query.action or None,
            actor=query.actor or None, personnel_id=query.personnel_id or None,
            sme_mois=query.sme_mois or None, date_from=query.date_from, date_to=query.date_to,
            text=query.text or None, personnel_nom=query.personnel_nom or None,
            limit=query.limit, require_table=True,
        )
    except Exception as exc:
        # Local mirrors may belong to an older database; do not misrepresent them as central results.
        raise RuntimeError("Lecture du journal central impossible. Vérifiez la connexion MySQL puis réessayez.") from exc


def entry_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """Complete payload, including older flattened JSONL entries."""
    if isinstance(entry.get("payload"), dict):
        return entry["payload"]
    metadata = {"ts_utc", "action", "actor", "module", "personnel_id", "sme_mois_context", "payload"}
    return {key: value for key, value in entry.items() if key not in metadata}


def _search_fold(value: Any) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value or "").casefold())
                   if not unicodedata.combining(c))


def entry_matches_query(entry: dict[str, Any], query: SensitiveAuditQuery) -> bool:
    """Compatibility matcher for archived entries; central SQL is not filtered twice."""
    query = validate_sensitive_audit_query(query)
    if query.action and entry.get("action") != query.action:
        return False
    for name, value in (("actor", query.actor), ("personnel_nom", query.personnel_nom)):
        if value and _search_fold(value) not in _search_fold(entry.get(name)):
            return False
    if query.personnel_id and query.personnel_id != str(entry.get("personnel_id") or ""):
        return False
    if query.sme_mois and not str(entry.get("sme_mois_context") or "").startswith(query.sme_mois):
        return False
    if query.text:
        values = [entry.get(key) for key in ("action", "actor", "personnel_id", "sme_mois_context")]
        values.append(json.dumps(entry_payload(entry), ensure_ascii=False, default=str))
        if not any(_search_fold(query.text) in _search_fold(value) for value in values):
            return False
    if query.date_from or query.date_to:
        try:
            ts = datetime.fromisoformat(str(entry.get("ts_utc") or "").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            local_day = ts.astimezone().date()
        except (ValueError, OverflowError, OSError):
            return False
        if query.date_from and local_day < query.date_from:
            return False
        if query.date_to and local_day > query.date_to:
            return False
    return True


def format_action_label(action: str) -> str:
    return ACTION_LABELS_FR.get(action, (action or "—").replace("_", " ").title())


def format_entry_detail(entry: dict[str, Any]) -> str:
    detail = str(entry.get("detail") or "").strip()
    if detail:
        return detail
    changes = entry.get("changes")
    if isinstance(changes, dict) and changes:
        parts: list[str] = []
        for field, delta in sorted(changes.items()):
            if not isinstance(delta, dict):
                continue
            parts.append(f"{field}: {delta.get('old')} → {delta.get('new')}")
        return " ; ".join(parts[:8])
    pl = entry.get("payload")
    if isinstance(pl, dict) and pl.get("detail"):
        return str(pl["detail"])
    return "—"
