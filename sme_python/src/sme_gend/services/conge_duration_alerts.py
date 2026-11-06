"""Congés en cours depuis plus de N jours (alerte tableau de bord / module Congés)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.agent_conge_service import padmin_to_conge_type
from sme_gend.services.numerique_padmin import PADMIN_CONGE_ADMIN, PADMIN_CONGE_EXP

CONGE_ALERT_MIN_DAYS = 30


def _is_conge_padmin_row(r: ListeGeneraleRow) -> bool:
    return padmin_to_conge_type(r.position_admin) is not None


def reference_date_for_conge_alert(settings: Settings) -> date:
    """Date de référence : aujourd'hui (mois actif) ou dernier jour du mois consulté."""
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.mysql_import_period import last_day_of_month

    ym = (resolve_export_mois(settings) or "").strip()
    today = date.today()
    if not ym:
        return today
    try:
        y_s, m_s = ym.split("-", 1)
        y, m = int(y_s), int(m_s)
    except (ValueError, TypeError):
        return today
    if y == today.year and m == today.month:
        return today
    end = last_day_of_month(ym)
    return end if end is not None else today


def _parse_row_date(text: str | None) -> date | None:
    if not (text or "").strip():
        return None
    d = parse_access_date(text)
    if d is not None:
        return d
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except ValueError:
        return None


def conge_is_ongoing_at(conge: AgentCongeRow | None, ref: date) -> bool:
    if conge is None or not conge.date_debut:
        return False
    debut = _parse_row_date(conge.date_debut)
    if debut is None:
        return False
    fin = _parse_row_date(conge.date_fin)
    if fin is not None and fin < ref:
        return False
    return debut <= ref


def elapsed_days_since_debut(conge: AgentCongeRow | None, ref: date) -> int | None:
    if conge is None or not conge.date_debut:
        return None
    debut = _parse_row_date(conge.date_debut)
    if debut is None or debut > ref:
        return None
    return (ref - debut).days


def is_long_duration_conge(
    conge: AgentCongeRow | None,
    ref: date,
    *,
    min_days: int = CONGE_ALERT_MIN_DAYS,
) -> bool:
    if not conge_is_ongoing_at(conge, ref):
        return False
    elapsed = elapsed_days_since_debut(conge, ref)
    if elapsed is None:
        return False
    return elapsed > min_days


@dataclass(frozen=True, slots=True)
class CongeMovementRow:
    liste: ListeGeneraleRow
    conge: AgentCongeRow | None
    elapsed_days: int | None
    is_long_alert: bool


def build_conge_movement_rows(
    liste_rows: list[ListeGeneraleRow],
    conges_by_legacy: dict[str, AgentCongeRow],
    settings: Settings,
    *,
    min_days: int = CONGE_ALERT_MIN_DAYS,
) -> list[CongeMovementRow]:
    ref = reference_date_for_conge_alert(settings)
    out: list[CongeMovementRow] = []
    for r in liste_rows:
        if not _is_conge_padmin_row(r):
            continue
        key = r.satellite_join_key() or r.id or ""
        conge = conges_by_legacy.get(key)
        if conge is None and r.id:
            conge = conges_by_legacy.get(r.id)
        elapsed = elapsed_days_since_debut(conge, ref) if conge else None
        alert = is_long_duration_conge(conge, ref, min_days=min_days)
        out.append(
            CongeMovementRow(
                liste=r,
                conge=conge,
                elapsed_days=elapsed,
                is_long_alert=alert,
            )
        )
    return out


def build_conge_history_rows(
    liste_rows: list[ListeGeneraleRow],
    conges_by_legacy: dict[str, AgentCongeRow],
    settings: Settings,
    *,
    min_days: int = CONGE_ALERT_MIN_DAYS,
) -> list[CongeMovementRow]:
    """Congés enregistrés (agent_conge) alors que le padmin n'est plus CongéAdmin / CongéExp."""
    ref = reference_date_for_conge_alert(settings)
    active_keys = {
        (r.satellite_join_key() or r.id or "").strip()
        for r in liste_rows
        if _is_conge_padmin_row(r)
    }
    out: list[CongeMovementRow] = []
    for r in liste_rows:
        if _is_conge_padmin_row(r):
            continue
        key = (r.satellite_join_key() or r.id or "").strip()
        if not key or key in active_keys:
            continue
        conge = conges_by_legacy.get(key)
        if conge is None and r.id:
            conge = conges_by_legacy.get(r.id)
        if conge is None:
            continue
        elapsed = elapsed_days_since_debut(conge, ref)
        alert = is_long_duration_conge(conge, ref, min_days=min_days)
        out.append(
            CongeMovementRow(
                liste=r,
                conge=conge,
                elapsed_days=elapsed,
                is_long_alert=alert,
            )
        )
    return out


def count_long_duration_conges(
    liste_rows: list[ListeGeneraleRow],
    conges_by_legacy: dict[str, AgentCongeRow],
    settings: Settings,
    *,
    min_days: int = CONGE_ALERT_MIN_DAYS,
) -> int:
    return sum(
        1
        for row in build_conge_movement_rows(
            liste_rows, conges_by_legacy, settings, min_days=min_days
        )
        if row.is_long_alert
    )


__all__ = [
    "CONGE_ALERT_MIN_DAYS",
    "CongeMovementRow",
    "build_conge_movement_rows",
    "build_conge_history_rows",
    "count_long_duration_conges",
    "is_long_duration_conge",
    "reference_date_for_conge_alert",
]
