"""Accès aux référentiels (structures, subdivisions, grades, fonctions)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.structure import StructureRow
from sme_gend.repositories.fonctions_repository import FonctionsRepository
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.structure_subdivision_repository import StructureSubdivisionRepository
from sme_gend.repositories.subdivision_repository import SubdivisionFlatRow, SubdivisionRepository


class ReferentielService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._struct_sub = StructureSubdivisionRepository(settings)
        self._liste_generale = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)
        self._fonctions = FonctionsRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._structures = StructureRepository(settings)
        self._subdivisions = SubdivisionRepository(settings)
        self._struct_sub = StructureSubdivisionRepository(settings)
        self._liste_generale = ListeGeneraleRepository(settings)
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

    def subdivisions_arme_for_structure(self, structure_code: str) -> list[str]:
        """Valeurs distinctes SubArme dans ListeGenerale pour cette structure (code TabStructure)."""
        return self._liste_generale.fetch_distinct_subarme_for_structure(structure_code)

    def subdivision_arme_combo_choices(self) -> list[str]:
        """Libellés pour liste déroulante SubArme : hiérarchie + valeurs déjà en base."""
        from_flat = [r.design_t for r in self._subdivisions.fetch_flat_hierarchy() if r.design_t]
        from_data = self._liste_generale.fetch_distinct_subarme()
        seen: set[str] = set()
        out: list[str] = []
        for x in from_flat + from_data:
            x = (x or "").strip()
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        out.sort(key=str.lower)
        return out

    def position_admin_choices(self) -> list[str]:
        """Positions administratives distinctes (liste générale)."""
        return self._liste_generale.fetch_distinct_padmin()

    def structure_insert(self, code: str, designation: str, ordre_tri: int | None) -> None:
        self._structures.insert(code, designation, ordre_tri)

    def structure_update(self, code: str, designation: str, ordre_tri: int | None) -> None:
        self._structures.update(code, designation, ordre_tri)

    def structure_delete(self, code: str) -> None:
        self._structures.delete(code)

    def grade_insert(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        self._grades.insert(code, libelle, pref_ord, effectif_g)

    def grade_update(
        self,
        code: str,
        libelle: str,
        pref_ord: int | None,
        effectif_g: int | None,
    ) -> None:
        self._grades.update(code, libelle, pref_ord, effectif_g)

    def grade_delete(self, code: str) -> None:
        self._grades.delete(code)
