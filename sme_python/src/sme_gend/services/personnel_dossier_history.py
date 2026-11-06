"""Historique des modifications agent (audit MySQL / JSONL)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sme_gend.services.audit_log import _all_audit_files

# Champs considérés comme « parcours professionnel » (affectation, carrière).
PARCOURS_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "structure",
        "grade",
        "position_admin",
        "fonction",
        "subdivision_arme",
        "date_grade",
        "date_service",
        "ndecision",
    }
)

# Champs « identité / profil » (hors parcours métier).
PROFIL_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "noms",
        "date_naissance",
        "lieu_naissance",
        "sexe",
        "matricule_militaire",
        "matricule_solde",
        "diplome",
        "date_obtention",
        "obs",
        "access_join_id",
        "ordre_tri",
        "presseance",
    }
)

FIELD_LABELS_FR: dict[str, str] = {
    "identity_merge": "Rapprochement des fiches",
    "noms": "Noms",
    "date_naissance": "Date de naissance",
    "lieu_naissance": "Lieu de naissance",
    "sexe": "Sexe",
    "grade": "Grade",
    "date_grade": "Date de grade",
    "structure": "Structure",
    "position_admin": "Position administrative",
    "fonction": "Fonction",
    "subdivision_arme": "Subdivision d’arme",
    "date_service": "Date de service",
    "ndecision": "N° décision",
    "matricule_militaire": "Matricule militaire",
    "matricule_solde": "Matricule solde",
    "diplome": "Diplôme",
    "date_obtention": "Date d’obtention",
    "obs": "Observations",
    "access_join_id": "Identifiant liaison Access",
    "ordre_tri": "Ordre de tri",
    "presseance": "Préséance",
}


@dataclass(slots=True)
class AuditTimelineEntry:
    """Une ligne d’audit « update » découpée par domaine."""

    ts_utc: str
    sme_mois_context: str | None
    career_changes: dict[str, dict[str, Any]]
    profile_changes: dict[str, dict[str, Any]]
    edited_by: str | None = None
    edit_motif: str | None = None


def _parse_changes(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if isinstance(v, dict) and "old" in v and "new" in v:
            out[str(k)] = {"old": v.get("old"), "new": v.get("new")}
    return out


def _split_changes(
    changes: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    career: dict[str, dict[str, Any]] = {}
    profil: dict[str, dict[str, Any]] = {}
    other: dict[str, dict[str, Any]] = {}
    for field, delta in changes.items():
        if field in PARCOURS_FIELD_NAMES:
            career[field] = delta
        elif field in PROFIL_FIELD_NAMES:
            profil[field] = delta
        else:
            other[field] = delta
    if other:
        profil.update(other)
    return career, profil


def _iter_audit_updates_mysql(personnel_id: str, *, settings=None) -> Iterator[dict[str, Any]]:
    pid = str(personnel_id).strip()
    if not pid:
        return
    try:
        from sme_gend.config import get_settings
        from sme_gend.repositories.app_config_repository import AppConfigRepository

        repository = AppConfigRepository(settings if settings is not None else get_settings())
        if not repository.table_exists("audit_journal"):
            raise RuntimeError("Journal central absent.")
        for obj in repository.read_personnel_audit_for(pid):
            if obj.get("action") == "update":
                yield obj
    except Exception as exc:
        raise RuntimeError("Historique central indisponible. Vérifiez MySQL puis actualisez le dossier.") from exc


def iter_audit_updates_for_person(personnel_id: str, *, settings=None, source="mysql") -> Iterator[dict[str, Any]]:
    pid = str(personnel_id).strip()
    if source == "mysql":
        yield from _iter_audit_updates_mysql(pid, settings=settings)
        return
    if source != "local":
        raise ValueError("Source d’historique inconnue.")
    # Explicit offline diagnostics only; UI never silently mixes database profiles.
    for path in _all_audit_files():
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict) or obj.get("action") != "update":
                    continue
                if str(obj.get("personnel_id", "")).strip() != pid:
                    continue
                yield obj


def build_audit_timeline(personnel_id: str, *, settings=None, source="mysql") -> list[AuditTimelineEntry]:
    """Événements triés chronologiquement (plus ancien → plus récent)."""
    entries: list[AuditTimelineEntry] = []
    for obj in iter_audit_updates_for_person(personnel_id, settings=settings, source=source):
        ts = str(obj.get("ts_utc", "")).strip()
        if not ts:
            continue
        changes = _parse_changes(obj.get("changes"))
        career, profil = _split_changes(changes)
        sme = obj.get("sme_mois_context")
        sme_s = str(sme).strip() if sme else None
        eb = obj.get("edited_by")
        em = obj.get("edit_motif")
        entries.append(
            AuditTimelineEntry(
                ts_utc=ts,
                sme_mois_context=sme_s or None,
                career_changes=career,
                profile_changes=profil,
                edited_by=str(eb).strip() if eb else None,
                edit_motif=str(em).strip() if em else None,
            )
        )
    def chronological(entry):
        try:
            stamp = datetime.fromisoformat(entry.ts_utc.replace("Z", "+00:00"))
            return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    entries.sort(key=chronological)
    return entries


def format_ts_display(ts_utc: str) -> str:
    """Affichage local lisible depuis ISO UTC."""
    t = ts_utc.strip()
    if not t:
        return "—"
    try:
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone()
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return ts_utc


def field_label(field: str) -> str:
    return FIELD_LABELS_FR.get(field, field.replace("_", " ").capitalize())


def fmt_val(v: Any) -> str:
    if v is None:
        return "—"
    s = str(v).strip()
    return s if s else "—"
