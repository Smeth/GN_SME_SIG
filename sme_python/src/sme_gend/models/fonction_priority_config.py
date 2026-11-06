"""Ordre prioritaire des fonctions pour le tri liste générale (lié à ref_fonction)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FonctionPriorityConfig:
    """Liste ordonnée d'identifiants ``ref_fonction.id``."""

    version: int = 1
    fonction_ids: list[int] = field(default_factory=list)

    write_token: str | None = field(default=None,repr=False,compare=False)
    _pending_write: object | None = field(default=None,repr=False,compare=False)
    _write_key: str | None = field(default=None,repr=False,compare=False)
    _write_database: object | None = field(default=None,repr=False,compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "fonction_ids": [int(x) for x in self.fonction_ids],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> FonctionPriorityConfig:
        if not raw:
            return cls()
        ids_raw = raw.get("fonction_ids")
        ids: list[int] = []
        if isinstance(ids_raw, list):
            for x in ids_raw:
                try:
                    ids.append(int(x))
                except (TypeError, ValueError):
                    continue
        return cls(
            version=int(raw.get("version", 1)),
            fonction_ids=ids,
        )
