"""Modèle aligné sur la table Access `ListeGenerale`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return None


@dataclass(slots=True)
class ListeGeneraleRow:
    """Une ligne de la liste générale (personnel)."""

    id: str
    noms: str | None
    date_naissance: datetime | None
    lieu_naissance: str | None
    grade: str | None
    date_grade: datetime | None
    date_service: str | None
    ndecision: str | None
    matricule_militaire: str | None
    matricule_solde: str | None
    position_admin: str | None
    fonction: str | None
    subdivision_arme: str | None
    diplome: str | None
    date_obtention: str | None
    structure: str | None
    obs: str | None
    ordre_tri: int | None
    presseance: int | None
    # Id Access (ListeGenerale.Id) pour TabMission / malade / … ; None en lecture Access directe.
    access_join_id: str | None = None
    sexe: str | None = None
    telephone: str | None = None
    adresse_residence: str | None = None

    # Import provenance: distinguish missing columns from explicit blanks.
    access_profile_fields: tuple[str, ...] = field(default=(), repr=False, compare=False)

    def satellite_join_key(self) -> str:
        """Clé pour dictionnaires satellites (missions, malades, …)."""
        aid = (self.access_join_id or "").strip()
        if aid:
            return aid
        return (self.id or "").strip()

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> ListeGeneraleRow:
        """Construit depuis le tuple renvoyé par pyodbc (ordre = SELECT du repository)."""
        def g(i: int) -> Any:
            return row[i] if i < len(row) else None

        return cls(
            id=str(g(0) or "").strip(),
            noms=_to_str(g(1)),
            date_naissance=_parse_date(g(2)),
            lieu_naissance=_to_str(g(3)),
            grade=_to_str(g(4)),
            date_grade=_parse_date(g(5)),
            date_service=_to_str(g(6)),
            ndecision=_to_str(g(7)),
            matricule_militaire=_to_str(g(8)),
            matricule_solde=_to_str(g(9)),
            position_admin=_to_str(g(10)),
            fonction=_to_str(g(11)),
            subdivision_arme=_to_str(g(12)),
            diplome=_to_str(g(13)),
            date_obtention=_to_str(g(14)),
            structure=_to_str(g(15)),
            obs=_to_str(g(16)),
            ordre_tri=_to_int(g(17)),
            presseance=_to_int(g(18)),
        )


def _to_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
