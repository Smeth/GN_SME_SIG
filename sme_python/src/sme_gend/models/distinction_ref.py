"""Référentiels médailles / décorations et attributions agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Sequence

DISTINCTION_KIND_MEDAILLE = "medaille"
DISTINCTION_KIND_DECORATION = "decoration"
DISTINCTION_KINDS = (DISTINCTION_KIND_MEDAILLE, DISTINCTION_KIND_DECORATION)


def _as_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


@dataclass(slots=True)
class DistinctionRefRow:
    id: int
    code: str
    libelle_court: str | None = None
    libelle_long: str | None = None
    categorie: str | None = None
    ordre_preseance: int | None = None
    actif: bool = True

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> DistinctionRefRow:
        return cls(
            id=int(row[0]),
            code=str(row[1] or "").strip(),
            libelle_court=str(row[2] or "").strip() or None if len(row) > 2 else None,
            libelle_long=str(row[3] or "").strip() or None if len(row) > 3 else None,
            categorie=str(row[4] or "").strip() or None if len(row) > 4 else None,
            ordre_preseance=int(row[5]) if len(row) > 5 and row[5] is not None else None,
            actif=bool(int(row[6])) if len(row) > 6 and row[6] is not None else True,
        )

    def display_label(self) -> str:
        return (self.libelle_court or self.libelle_long or self.code or "—").strip()


@dataclass(slots=True)
class AgentDistinctionRow:
    id: int
    agent_id: int
    kind: str
    ref_medaille_id: int | None = None
    ref_decoration_id: int | None = None
    libelle: str = ""
    date_attribution: date | None = None
    date_publication_journal: date | None = None
    numero_arrete: str | None = None
    date_arrete: date | None = None
    citation: str | None = None
    source: str | None = None

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> AgentDistinctionRow:
        kind = str(row[2] or "").strip().lower()
        return cls(
            id=int(row[0]),
            agent_id=int(row[1]),
            kind=kind,
            ref_medaille_id=int(row[3]) if len(row) > 3 and row[3] is not None else None,
            ref_decoration_id=int(row[4]) if len(row) > 4 and row[4] is not None else None,
            libelle=str(row[5] or "").strip(),
            date_attribution=_as_date(row[6]) if len(row) > 6 else None,
            date_publication_journal=_as_date(row[7]) if len(row) > 7 else None,
            numero_arrete=str(row[8] or "").strip() or None if len(row) > 8 else None,
            date_arrete=_as_date(row[9]) if len(row) > 9 else None,
            citation=str(row[10] or "").strip() or None if len(row) > 10 else None,
            source=str(row[11] or "").strip() or None if len(row) > 11 else None,
        )
