"""Référentiel fonction (ref_fonction)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(slots=True)
class FonctionRefRow:
    id: int
    libelle: str

    write_token: tuple[str, str, str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_sequence(cls, row: Sequence[Any]) -> FonctionRefRow:
        return cls(id=int(row[0]), libelle=str(row[1] or "").strip(),
            write_token=("fonction", str(int(row[0])), str(row[2])) if len(row)>2 and row[2] is not None else None)
