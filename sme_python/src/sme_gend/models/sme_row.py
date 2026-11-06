"""Table `TabSme` — périodes mensuelles SME (schéma cible : Codsme, mois, Statut)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence


@dataclass(slots=True)
class SmeRow:
    """Une ligne TabSme."""

    codsme: str
    mois: str
    statut: str

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> SmeRow:
        return cls(
            codsme=str(row[0] or "").strip(),
            mois=_fmt_mois(row[1]),
            statut=str(row[2] or "").strip(),
        )


def _fmt_mois(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m")
    return str(v).strip()
