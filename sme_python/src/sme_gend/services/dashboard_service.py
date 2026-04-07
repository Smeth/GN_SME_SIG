"""Agrégats pour le tableau de bord d'accueil."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.repositories.grade_repository import GradeRepository


def _is_present(padmin: str | None) -> bool:
    if not padmin:
        return False
    return "present" in padmin.strip().lower().replace("é", "e").replace("è", "e")


def _top_n_with_other(counter: Counter[str], n: int) -> list[tuple[str, int]]:
    """Les n plus fréquents ; le reste est agrégé dans « Autres »."""
    items = counter.most_common()
    if len(items) <= n:
        return items
    top = items[:n]
    rest = sum(c for _, c in items[n:])
    return top + [("Autres", rest)]


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    total_effectifs: int
    presents: int
    hors_present: int
    taux_presence_pct: float
    positions: list[tuple[str, int]]
    structures_distinctes_effectifs: int
    nb_structures_referentiel: int
    nb_grades_referentiel: int
    nb_subdivisions_hierarchie: int
    grades_distribution: list[tuple[str, int]]
    structures_distribution: list[tuple[str, int]]


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._grades = GradeRepository(settings)

    def snapshot(self) -> DashboardSnapshot:
        rows = self._liste.fetch_all()
        total = len(rows)
        presents = sum(1 for r in rows if _is_present(r.position_admin))
        hors = total - presents
        taux = (100.0 * presents / total) if total else 0.0

        positions_counter: Counter[str] = Counter()
        grade_counter: Counter[str] = Counter()
        struct_counter: Counter[str] = Counter()
        for r in rows:
            lab = (r.position_admin or "").strip() or "(Non renseigné)"
            positions_counter[lab] += 1
            g = (r.grade or "").strip() or "(Non renseigné)"
            grade_counter[g] += 1
            st = (r.structure or "").strip() or "(Sans structure)"
            struct_counter[st] += 1

        positions = sorted(positions_counter.items(), key=lambda x: (-x[1], x[0]))
        grades_distribution = _top_n_with_other(grade_counter, 10)
        structures_distribution = _top_n_with_other(struct_counter, 10)

        structs_eff = len({(r.structure or "").strip() for r in rows if (r.structure or "").strip()})
        nb_struct = len(self._structures.fetch_all())
        nb_grades = len(self._grades.fetch_all())
        from sme_gend.repositories.subdivision_repository import SubdivisionRepository

        nb_sub = len(SubdivisionRepository(self._settings).fetch_flat_hierarchy())

        return DashboardSnapshot(
            total_effectifs=total,
            presents=presents,
            hors_present=hors,
            taux_presence_pct=taux,
            positions=positions,
            structures_distinctes_effectifs=structs_eff,
            nb_structures_referentiel=nb_struct,
            nb_grades_referentiel=nb_grades,
            nb_subdivisions_hierarchie=nb_sub,
            grades_distribution=grades_distribution,
            structures_distribution=structures_distribution,
        )
