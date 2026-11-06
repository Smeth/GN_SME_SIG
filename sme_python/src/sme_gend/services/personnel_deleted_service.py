"""Fiches retirées de la liste — liste depuis l'audit et restauration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services import audit_log


class DeletedPersonnelStatus(str, Enum):
    RESTORABLE = "restorable"
    ALREADY_PRESENT = "already_present"
    MATRICULE_CONFLICT = "matricule_conflict"
    NO_SNAPSHOT = "no_snapshot"


@dataclass(slots=True)
class DeletedPersonnelEntry:
    ts_utc: str
    edited_by: str
    personnel_id: str
    sme_mois_context: str
    deleted_career_id: str
    loss_motif: str
    row: ListeGeneraleRow | None
    status: DeletedPersonnelStatus
    status_detail: str

    @property
    def display_name(self) -> str:
        if self.row and self.row.noms:
            return self.row.noms
        return self.personnel_id or "?"


def _norm_mat(value: str | None) -> str:
    return (value or "").strip().upper()


def _repo_impl(repo: ListeGeneraleRepository):
    return getattr(repo, "_impl", repo)


def _resolve_deleted_agent_id(
    impl,
    row: ListeGeneraleRow,
    deleted_career_id: str,
) -> int | None:
    cid = (deleted_career_id or row.id or "").strip()
    fetch_agent = getattr(impl, "fetch_agent_id_for_career", None)
    if cid and callable(fetch_agent):
        aid = fetch_agent(cid)
        if aid:
            return aid
    fetch_legacy = getattr(impl, "fetch_agent_id_by_legacy", None)
    leg = (row.access_join_id or "").strip()
    if leg and callable(fetch_legacy):
        return fetch_legacy(leg)
    return None


def _same_person_in_liste(
    row: ListeGeneraleRow,
    other: ListeGeneraleRow,
    *,
    deleted_agent_id: int | None,
    other_agent_id: int | None,
) -> bool:
    if deleted_agent_id and other_agent_id and deleted_agent_id == other_agent_id:
        return True
    leg = (row.access_join_id or "").strip()
    oleg = (other.access_join_id or "").strip()
    if leg and oleg and leg == oleg:
        return True
    return False


def assess_deleted_restore(
    repo: ListeGeneraleRepository,
    row: ListeGeneraleRow,
    deleted_career_id: str,
) -> tuple[DeletedPersonnelStatus, str]:
    cid = (deleted_career_id or row.id or "").strip()
    impl = _repo_impl(repo)
    fetch_agent = getattr(impl, "fetch_agent_id_for_career", None)
    segment_exists = getattr(impl, "career_segment_exists", None)
    deleted_agent_id = _resolve_deleted_agent_id(impl, row, deleted_career_id)
    if cid and repo.fetch_by_id(cid):
        return DeletedPersonnelStatus.ALREADY_PRESENT, "Déjà présent dans la liste du mois SME."
    all_rows = repo.fetch_all()
    for other in all_rows:
        other_agent: int | None = None
        if callable(fetch_agent) and other.id:
            other_agent = fetch_agent(str(other.id))
        if _same_person_in_liste(
            row,
            other,
            deleted_agent_id=deleted_agent_id,
            other_agent_id=other_agent,
        ):
            return (
                DeletedPersonnelStatus.ALREADY_PRESENT,
                f"Déjà réintégré en liste (carrière id {other.id}).",
            )
    if (
        cid
        and callable(segment_exists)
        and segment_exists(cid)
        and not repo.fetch_by_id(cid)
    ):
        return DeletedPersonnelStatus.RESTORABLE, ""
    mat_m = _norm_mat(row.matricule_militaire)
    mat_s = _norm_mat(row.matricule_solde)
    for other in all_rows:
        other_agent = None
        if callable(fetch_agent) and other.id:
            other_agent = fetch_agent(str(other.id))
        if _same_person_in_liste(
            row,
            other,
            deleted_agent_id=deleted_agent_id,
            other_agent_id=other_agent,
        ):
            continue
        if mat_m and _norm_mat(other.matricule_militaire) == mat_m:
            return (
                DeletedPersonnelStatus.MATRICULE_CONFLICT,
                f"Matricule militaire déjà utilisé par « {other.noms or other.id} ».",
            )
        if mat_s and _norm_mat(other.matricule_solde) == mat_s:
            return (
                DeletedPersonnelStatus.MATRICULE_CONFLICT,
                f"Matricule solde déjà utilisé par « {other.noms or other.id} ».",
            )
    return DeletedPersonnelStatus.RESTORABLE, ""


def _entry_from_audit(raw: dict[str, Any]) -> DeletedPersonnelEntry | None:
    before = raw.get("before")
    if not isinstance(before, dict):
        before = {}
    row = audit_log.row_from_audit_dict(before)
    deleted_career_id = str(before.get("id") or raw.get("personnel_id") or "").strip()
    if not deleted_career_id and row:
        deleted_career_id = str(row.id or "").strip()
    ts = str(raw.get("ts_utc") or "")
    motif = str(raw.get("edit_motif") or "").strip()
    return DeletedPersonnelEntry(
        ts_utc=ts,
        edited_by=str(raw.get("edited_by") or raw.get("actor") or "").strip(),
        personnel_id=str(raw.get("personnel_id") or "").strip(),
        sme_mois_context=str(raw.get("sme_mois_context") or "").strip(),
        deleted_career_id=deleted_career_id,
        loss_motif=motif,
        row=row,
        status=DeletedPersonnelStatus.NO_SNAPSHOT,
        status_detail="",
    )


def list_deleted_personnel(
    settings: Settings,
    *,
    limit: int = 400,
) -> list[DeletedPersonnelEntry]:
    """Suppressions récentes (une entrée par id carrière supprimé, la plus récente)."""
    repo_cfg = AppConfigRepository(settings)
    repo = ListeGeneraleRepository(settings)
    raw_rows = repo_cfg.list_personnel_delete_audit(limit=limit)
    by_career: dict[str, DeletedPersonnelEntry] = {}
    for raw in raw_rows:
        entry = _entry_from_audit(raw)
        if entry is None:
            continue
        key = entry.deleted_career_id or entry.personnel_id or entry.ts_utc
        if not key:
            continue
        if key in by_career:
            continue
        if entry.row is None:
            entry.status = DeletedPersonnelStatus.NO_SNAPSHOT
            entry.status_detail = "Snapshot d'audit incomplet — restauration impossible."
        else:
            st, detail = assess_deleted_restore(repo, entry.row, entry.deleted_career_id)
            entry.status = st
            entry.status_detail = detail
        by_career[key] = entry
    items = list(by_career.values())

    def sort_key(e: DeletedPersonnelEntry) -> datetime:
        try:
            stamp = datetime.fromisoformat(e.ts_utc.replace("Z", "+00:00"))
            return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    items.sort(key=sort_key, reverse=True)
    return items


def format_deleted_status(entry: DeletedPersonnelEntry) -> str:
    if entry.status == DeletedPersonnelStatus.RESTORABLE:
        return "Restaurable"
    if entry.status == DeletedPersonnelStatus.ALREADY_PRESENT:
        return "Déjà en liste"
    if entry.status == DeletedPersonnelStatus.MATRICULE_CONFLICT:
        return "Conflit matricule"
    return "Snapshot manquant"
