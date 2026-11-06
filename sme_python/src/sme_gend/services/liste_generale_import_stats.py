"""Statistiques détaillées import liste générale Access → MySQL."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ListeGeneraleImportStats:
    rows_read: int = 0
    ids_empty_source: int = 0
    ids_auto_generated: int = 0
    ids_ignored: int = 0
    matricule_duplicates_kept: int = 0
    matricule_merged: int = 0
    agents_distinct: int = 0
    inserted: int = 0
    updated: int = 0
    duplicate_access_ids_resolved: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "rows_read": self.rows_read,
            "ids_empty_source": self.ids_empty_source,
            "ids_auto_generated": self.ids_auto_generated,
            "ids_ignored": self.ids_ignored,
            "matricule_duplicates_kept": self.matricule_duplicates_kept,
            "matricule_merged": self.matricule_merged,
            "agents_distinct": self.agents_distinct,
            "liste_inserted": self.inserted,
            "liste_updated": self.updated,
            "duplicate_access_ids_resolved": self.duplicate_access_ids_resolved,
        }
