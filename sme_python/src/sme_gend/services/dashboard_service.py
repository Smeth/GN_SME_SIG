"""Agrégats pour le tableau de bord d'accueil + données TabSme (période mensuelle)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sme_gend.config.settings import Settings
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.repositories.structure_repository import StructureRepository


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


def _statut_score(statut: str) -> float:
    """Score 0–1 pour tendance (Statut texte ou numérique)."""
    s = (statut or "").strip().lower()
    if any(x in s for x in ("ouvert", "open", "en cours", "actif", "valid")):
        return 1.0
    if any(x in s for x in ("ferme", "fermé", "clos", "closed", "inactif")):
        return 0.0
    try:
        v = float(statut.replace(",", "."))
        if 0.0 <= v <= 1.0:
            return v
        if v > 1.0:
            return min(v / 100.0, 1.0)
        return v
    except ValueError:
        return 0.5


def _sort_key_mois(mois: str) -> tuple:
    """Tri chronologique approximatif pour libellés mois."""
    t = (mois or "").strip()
    m = re.match(r"^(\d{4})-(\d{1,2})", t)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m2 = re.match(r"^(\d{1,2})/(\d{4})", t)
    if m2:
        return (int(m2.group(2)), int(m2.group(1)))
    try:
        dt = datetime.strptime(t[:10], "%Y-%m-%d")
        return (dt.year, dt.month)
    except ValueError:
        return (9999, 99)


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
    sme_table_ok: bool
    sme_rows: tuple[SmeRow, ...]
    sme_trend_points: tuple[tuple[str, float], ...]


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._grades = GradeRepository(settings)
        self._sme = SmeRepository(settings)

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

        sme_table_ok = self._sme.table_exists()
        sme_rows_list: list[SmeRow] = []
        trend: list[tuple[str, float]] = []
        if sme_table_ok:
            try:
                sme_rows_list = self._sme.fetch_all()
            except Exception:
                sme_table_ok = False
            else:
                sme_rows_list.sort(key=lambda r: _sort_key_mois(r.mois))
                trend = [(r.mois, _statut_score(r.statut)) for r in sme_rows_list]

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
            sme_table_ok=sme_table_ok,
            sme_rows=tuple(sme_rows_list),
            sme_trend_points=tuple(trend),
        )
