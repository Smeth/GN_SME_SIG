"""Fonctions : référentiel MySQL ref_fonction + liaisons structure."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.repositories.mysql_fonction_structure_repository import (
    MySQLFonctionStructureRepository,
)

__all__ = ["FonctionsRepository"]


class FonctionsRepository:
    def __init__(self, settings: Settings) -> None:
        from sme_gend.repositories.mysql_bundle_repositories import MySQLFonctionsRepository

        self._impl = MySQLFonctionsRepository(settings)
        self._links = MySQLFonctionStructureRepository(settings)

    def fetch_distinct(self) -> list[str]:
        return self._impl.fetch_distinct()

    def fetch_all(self) -> list[FonctionRefRow]:
        return self._impl.fetch_all()

    def insert(self, libelle: str) -> int:
        return self._impl.insert(libelle)

    def update(self, fonction_id: int, old_libelle: str, new_libelle: str, *, expected=None) -> None:
        return self._impl.update(fonction_id, old_libelle, new_libelle, expected=expected)

    def delete(self, fonction_id: int, *, expected=None) -> None:
        return self._impl.delete(fonction_id, expected=expected)

    def count_usage(self, libelle: str, fonction_id: int | None = None) -> int:
        return self._impl.count_usage(libelle, fonction_id)

    def fetch_usage_counts(self) -> dict[int, int]:
        return self._impl.fetch_usage_counts()

    def rename_impact(self, libelle, fonction_id):
        return self._impl.rename_impact(libelle, fonction_id)

    def fetch_choice_counts_by_structure(self) -> dict[str, int]:
        return self._links.fetch_choice_counts_by_structure()

    def fetch_for_structure(self, structure_code: str) -> list[str]:
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        if structure_fonctions_non_liees(structure_code):
            return []
        linked = self._links.fetch_libelles_for_structure(structure_code)
        if linked:
            return linked
        return self._links.fetch_observed_libelles_for_structure(structure_code)

    def fetch_linked_for_structure(self, code):
        return self._links.fetch_libelles_for_structure(code)

    def fetch_linked_labels_by_structure(self):
        return self._links.fetch_linked_labels_by_structure()

    def structure_codes_for(self, fonction_id: int) -> list[str]:
        return self._links.fetch_structure_codes_for_fonction(fonction_id)

    def links_summary(self) -> dict[int, list[str]]:
        return self._links.fetch_links_summary()

    def links_snapshot_for_fonction(self,fonction_id):
        return self._links.load_links_fonction_snapshot(fonction_id)

    def links_snapshot_for_structure(self,code):
        return self._links.load_links_structure_snapshot(code)

    def links_snapshot_all(self):
        return self._links.load_links_all_snapshot()

    def validate_links_snapshot(self,expected):
        from sme_gend.repositories.reference_collection_guard import require_collection,check_collection
        from sme_gend.services.shared_reference_write import shared_reference_write
        require_collection(expected,"links_all")
        with shared_reference_write(self._links._settings):
            with self._links._connect() as connection:
                with connection.cursor() as cursor:check_collection(cursor,expected)

    def merge_structure_links(self,fonction_id,codes):
        self._links.merge_links_for_fonction(fonction_id,codes)

    def set_structure_links(self, fonction_id: int, structure_codes: list[str], *, expected=None) -> None:
        return self._links.set_links_for_fonction(fonction_id, structure_codes, expected=expected)

    def set_fonction_links_for_structure(
        self, structure_code: str, fonction_libelles: list[str], *, expected=None
    ) -> None:
        return self._links.set_links_for_structure(structure_code, fonction_libelles, expected=expected)

    def ensure_structure_link(self, structure_code: str, fonction_libelle: str) -> None:
        return self._links.ensure_link(structure_code, fonction_libelle)

    def backfill_structure_links_from_carriere(self) -> int:
        return self._links.backfill_from_carriere()

    def fetch_observed_usage(self) -> list[tuple[str, str, int]]:
        return self._links.fetch_observed_usage()
