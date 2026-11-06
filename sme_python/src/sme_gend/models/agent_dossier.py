"""Diplômes et passations de grade — dossier agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

DIPLOME_MILITAIRE = "MILITAIRE"
DIPLOME_CIVIL = "CIVIL"
DIPLOME_DEFAULT = DIPLOME_MILITAIRE
DIPLOME_CATEGORIES = (DIPLOME_MILITAIRE, DIPLOME_CIVIL)


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


@dataclass(slots=True)
class AgentDiplomeRow:
    id: int
    agent_id: int
    categorie: str
    libelle: str
    date_obtention: date | None = None
    lieu_obtention: str | None = None
    mention: str | None = None
    etablissement: str | None = None
    obs: str | None = None
    ref_diplome_id: int | None = None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> AgentDiplomeRow:
        return cls(
            id=int(row[0]),
            agent_id=int(row[1]),
            categorie=str(row[2] or DIPLOME_DEFAULT).strip().upper(),
            libelle=str(row[3] or "").strip(),
            date_obtention=_as_date(row[4]) if len(row) > 4 else None,
            lieu_obtention=str(row[5] or "").strip() or None if len(row) > 5 else None,
            mention=str(row[6] or "").strip() or None if len(row) > 6 else None,
            etablissement=str(row[7] or "").strip() or None if len(row) > 7 else None,
            obs=str(row[8] or "").strip() or None if len(row) > 8 else None,
            ref_diplome_id=int(row[9]) if len(row) > 9 and row[9] is not None else None,
        )


@dataclass(slots=True)
class AgentPassationGradeRow:
    id: int
    agent_id: int
    grade_libelle: str
    date_passation: date | None = None
    ndecision: str | None = None
    obs: str | None = None
    grade_id: int | None = None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> AgentPassationGradeRow:
        return cls(
            id=int(row[0]),
            agent_id=int(row[1]),
            grade_libelle=str(row[2] or "").strip(),
            date_passation=_as_date(row[3]) if len(row) > 3 else None,
            ndecision=str(row[4] or "").strip() or None if len(row) > 4 else None,
            obs=str(row[5] or "").strip() or None if len(row) > 5 else None,
            grade_id=int(row[6]) if len(row) > 6 and row[6] is not None else None,
        )


@dataclass(slots=True)
class AgentAffectationRow:
    id: int
    agent_id: int
    structure_code: str
    date_affectation: date
    fonction: str | None = None
    subdivision_arme: str | None = None
    sme_periode_mois: str | None = None
    obs: str | None = None
    structure_id: int | None = None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> AgentAffectationRow:
        return cls(
            id=int(row[0]),
            agent_id=int(row[1]),
            structure_code=str(row[2] or "").strip(),
            date_affectation=_as_date(row[3]) or date.today(),
            fonction=str(row[4] or "").strip() or None if len(row) > 4 else None,
            subdivision_arme=str(row[5] or "").strip() or None if len(row) > 5 else None,
            sme_periode_mois=str(row[6] or "").strip() or None if len(row) > 6 else None,
            obs=str(row[7] or "").strip() or None if len(row) > 7 else None,
            structure_id=int(row[8]) if len(row) > 8 and row[8] is not None else None,
        )
