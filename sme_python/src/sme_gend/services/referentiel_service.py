"""Accès aux référentiels (structures, subdivisions, grades, fonctions)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.fonctions_repository import FonctionsRepository
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.structure_subdivision_repository import StructureSubdivisionRepository
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow, SubdivisionRepository


class ReferentielService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._struct_sub = StructureSubdivisionRepository(settings)
        self._grades = GradeRepository(settings)
        self._fonctions = FonctionsRepository(settings)

    def structures(self) -> list[StructureRow]:
        return self._structures.fetch_all()

    def subdivisions_flat(self) -> list[SubdivisionFlatRow]:
        return self._subdivisions.fetch_flat_hierarchy()

    def grades(self) -> list[GradeRefRow]:
        return self._grades.fetch_all()

    def fonctions_distinctes(self) -> list[str]:
        return self._fonctions.fetch_distinct()

    def structure_subdivision_pairs(self) -> list[tuple[str, str]]:
        """Couples (code structure, subdivision d'arme) présents dans ListeGenerale."""
        return self._struct_sub.fetch_distinct_pairs()
