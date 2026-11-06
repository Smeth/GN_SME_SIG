"""Comparaison de snapshots carrière mensuels (import dossier)."""

from __future__ import annotations

from typing import Any

from sme_gend.models.agent_evenement import CarriereSnapshotRow

_SNAPSHOT_DIFF_FIELDS: tuple[tuple[str, str], ...] = (
    ("grade", "grade"),
    ("structure", "structure"),
    ("position_admin", "position_admin"),
    ("fonction", "fonction"),
    ("subdivision_arme", "subdivision_arme"),
    ("date_grade", "date_grade"),
    ("date_service", "date_service"),
    ("ndecision", "ndecision"),
    ("diplome", "diplome"),
    ("date_obtention", "date_obtention"),
)

_MOIS_FR = (
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
)


def fmt_mois_label(ym: str) -> str:
    try:
        y, m = ym.split("-")
        return f"{_MOIS_FR[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return ym


def _snapshot_value(s: CarriereSnapshotRow, attr: str) -> str | None:
    v = getattr(s, attr, None)
    if v is None:
        return None
    t = str(v).strip()
    return t or None


def diff_carriere_snapshots(
    before: CarriereSnapshotRow,
    after: CarriereSnapshotRow,
) -> dict[str, dict[str, Any]]:
    """Retourne les champs modifiés entre deux situations mensuelles."""
    changes: dict[str, dict[str, Any]] = {}
    for attr, audit_key in _SNAPSHOT_DIFF_FIELDS:
        old = _snapshot_value(before, attr)
        new = _snapshot_value(after, attr)
        from sme_gend.services.access_date_parse import parse_access_date
        def comparable(value):
            if attr.startswith("date_"):
                return parse_access_date(value) or value
            return " ".join((value or "").split()).casefold() or None
        if comparable(old) != comparable(new):
            changes[audit_key] = {"old": old, "new": new}
    return changes
