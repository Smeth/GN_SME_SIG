"""Construction du parcours et de l’historique profil à partir de ``personnel_audit.jsonl``."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from sme_gend.services.audit_log import personnel_audit_file_path

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
    "noms": "Noms",
    "date_naissance": "Date de naissance",
    "lieu_naissance": "Lieu de naissance",
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


def iter_audit_updates_for_person(personnel_id: str) -> Iterator[dict[str, Any]]:
    path: Path = personnel_audit_file_path()
    if not path.is_file():
        return
    pid = str(personnel_id).strip()
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("action") != "update":
                continue
            if str(obj.get("personnel_id", "")).strip() != pid:
                continue
            yield obj


def build_audit_timeline(personnel_id: str) -> list[AuditTimelineEntry]:
    """Événements triés chronologiquement (plus ancien → plus récent)."""
    entries: list[AuditTimelineEntry] = []
    for obj in iter_audit_updates_for_person(personnel_id):
        ts = str(obj.get("ts_utc", "")).strip()
        if not ts:
            continue
        changes = _parse_changes(obj.get("changes"))
        career, profil = _split_changes(changes)
        sme = obj.get("sme_mois_context")
        sme_s = str(sme).strip() if sme else None
        entries.append(
            AuditTimelineEntry(
                ts_utc=ts,
                sme_mois_context=sme_s or None,
                career_changes=career,
                profile_changes=profil,
            )
        )
    entries.sort(key=lambda e: e.ts_utc)
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
        if dt.tzinfo is not None:
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
