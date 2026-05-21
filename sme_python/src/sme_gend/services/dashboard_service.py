"""Agrégats pour le tableau de bord d'accueil + données TabSme (période mensuelle)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.sme_row import SmeRow
from sme_gend.repositories.gain_perte_repository import GainPerteRepository
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.repositories.structure_repository import StructureRepository
from sme_gend.services.detaches_service import STRUCTURE_CODE_DETACHE, is_structure_detache_row
from sme_gend.padmin_present import padmin_counts_as_present
from sme_gend.services import numerique_padmin as npa


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


def _padmin_eq(padmin: str | None, code: str) -> bool:
    """Égalité insensible à la casse (libellés Access)."""
    a = (padmin or "").strip()
    if not a:
        return False
    return a == code or a.casefold() == code.casefold()


def _structure_eq(structure: str | None, code: str) -> bool:
    """Code structure liste générale — insensible à la casse (ex. DETACHE, detache)."""
    s = (structure or "").strip()
    if not s:
        return False
    c = (code or "").strip()
    return s == c or s.casefold() == c.casefold()


def _fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", "\u202f")


def _subtitle_effectif_structure_detache(rows: list[ListeGeneraleRow], *, max_items: int = 8) -> str | None:
    """Sous-titre : répartition par position administrative pour Structure = DETACHE."""
    sel = [r for r in rows if is_structure_detache_row(r)]
    if not sel:
        return (
            f"Aucun personnel avec structure « {STRUCTURE_CODE_DETACHE} »."
        )
    c: Counter[str] = Counter()
    for r in sel:
        lab = (r.position_admin or "").strip() or "(Padmin vide)"
        c[lab] += 1
    parts = [f"{name} : {_fmt_int(n)}" for name, n in c.most_common(max_items)]
    out = "Padmin — " + " · ".join(parts)
    if len(c) > max_items:
        out += f" — +{len(c) - max_items} position(s)"
    return out


def _is_stage_interne(padmin: str | None) -> bool:
    x = " ".join((padmin or "").strip().split()).casefold().replace("é", "e")
    return x == "stageinterne" or x == "stage interne"


def _is_punis_row(row: ListeGeneraleRow) -> bool:
    return _padmin_eq(row.position_admin, npa.PADMIN_PUNIS) or _padmin_eq(
        row.position_admin, npa.PADMIN_PRISON_MA
    )


@dataclass(frozen=True, slots=True)
class DashboardKpiCard:
    """Une carte KPI (indicateurs principaux). variant : default | success | info | brand | muted | alert | highlight."""

    kpi_id: str
    title: str
    value: str
    subtitle: str | None
    variant: str


def build_main_kpi_cards(
    total: int,
    presents: int,
    taux_pct: float,
    rows: list[ListeGeneraleRow],
) -> tuple[DashboardKpiCard, ...]:
    """
    Cartes « indicateurs principaux » : effectifs + répartition Padmin (données ListeGenerale).
    Aligné sur les codes utilisés pour la situation numérique (numerique_padmin).
    """
    c_present = sum(1 for r in rows if padmin_counts_as_present(r.position_admin, "present"))
    c_det = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_DETACHE))
    c_si = sum(1 for r in rows if _is_stage_interne(r.position_admin))
    c_miss = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_MISSION))
    c_mal = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_MALADE))
    c_des = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_DESERTEUR))
    c_sl = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_STAGE_LOCAL))
    c_se = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_STAGE_EX))
    c_imr = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_INST_MR))
    c_ca = sum(1 for r in rows if _padmin_eq(r.position_admin, npa.PADMIN_CONGE_ADMIN))
    c_pun = sum(1 for r in rows if _is_punis_row(r))
    c_struct_detache = sum(1 for r in rows if is_structure_detache_row(r))

    sub = f"{_fmt_int(presents)} / {_fmt_int(total)} présents" if total else None

    struct_det_sub = _subtitle_effectif_structure_detache(rows)

    cards: list[DashboardKpiCard] = [
        DashboardKpiCard(
            "effectifs",
            "Effectifs enregistrés",
            _fmt_int(total),
            None,
            "default",
        ),
        DashboardKpiCard(
            "present",
            "Présent",
            _fmt_int(c_present),
            None,
            "success",
        ),
        DashboardKpiCard(
            "detache",
            "Détaché",
            _fmt_int(c_det),
            None,
            "info",
        ),
        DashboardKpiCard(
            "structure_detache",
            "Structure détachement",
            _fmt_int(c_struct_detache),
            struct_det_sub,
            "info",
        ),
        DashboardKpiCard(
            "stage_interne",
            "StageInterne",
            _fmt_int(c_si),
            None,
            "brand",
        ),
        DashboardKpiCard(
            "mission",
            "Mission",
            _fmt_int(c_miss),
            None,
            "info",
        ),
        DashboardKpiCard(
            "malade",
            "Malade",
            _fmt_int(c_mal),
            None,
            "muted",
        ),
        DashboardKpiCard(
            "deserteur",
            "Déserteur",
            _fmt_int(c_des),
            None,
            "alert",
        ),
        DashboardKpiCard(
            "stage_local",
            "StageLocal",
            _fmt_int(c_sl),
            None,
            "muted",
        ),
        DashboardKpiCard(
            "stage_ex",
            "StageEx",
            _fmt_int(c_se),
            None,
            "muted",
        ),
        DashboardKpiCard(
            "inst_mr",
            "Inst Mise Route",
            _fmt_int(c_imr),
            None,
            "muted",
        ),
        DashboardKpiCard(
            "conge_admin",
            "CongéAdmin",
            _fmt_int(c_ca),
            None,
            "info",
        ),
        DashboardKpiCard(
            "punis",
            "Punis",
            _fmt_int(c_pun),
            None,
            "muted",
        ),
        DashboardKpiCard(
            "taux",
            "Taux de présence",
            f"{taux_pct:.1f}\u00a0%",
            sub,
            "highlight",
        ),
    ]
    return tuple(cards)


def build_gain_perte_kpi_cards(
    mois: str,
    n_deces: int,
    n_gains: int,
    n_pertes: int,
) -> tuple[DashboardKpiCard, ...]:
    """Cartes KPI pour les gains & pertes du mois (TabGainPerte)."""
    return (
        DashboardKpiCard(
            "deces",
            "Décès",
            _fmt_int(n_deces),
            f"mois {mois}" if mois else None,
            "alert" if n_deces else "muted",
        ),
        DashboardKpiCard(
            "gains_mois",
            "Gains du mois",
            _fmt_int(n_gains),
            f"mois {mois}" if mois else None,
            "success" if n_gains else "muted",
        ),
        DashboardKpiCard(
            "pertes_mois",
            "Pertes du mois",
            _fmt_int(n_pertes),
            f"dont {n_deces} décès" if n_deces else None,
            "highlight" if n_pertes else "muted",
        ),
    )


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    """Instantané tableau de bord. ``presents`` = positions « présent » (mot-clé) uniquement ; le stage interne n’est plus inclus dans ce total (voir liste des présents). grades_effectifs_detail : (grade, effectif, % total, écart vs uniforme)."""

    total_effectifs: int
    presents: int
    indisponibles: int
    taux_presence_pct: float
    main_kpi_cards: tuple[DashboardKpiCard, ...]
    positions: list[tuple[str, int]]
    structures_distinctes_effectifs: int
    nb_structures_referentiel: int
    nb_grades_referentiel: int
    nb_subdivisions_hierarchie: int
    grades_distribution: list[tuple[str, int]]
    structures_distribution: list[tuple[str, int]]
    grades_effectifs_detail: tuple[tuple[str, int, float, float], ...]
    sme_table_ok: bool
    sme_rows: tuple[SmeRow, ...]
    sme_trend_points: tuple[tuple[str, float], ...]
    gain_perte_cards: tuple[DashboardKpiCard, ...] = ()
    gain_perte_rows: tuple[GainPerteRow, ...] = ()


class DashboardService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._grades = GradeRepository(settings)
        self._sme = SmeRepository(settings)
        self._gain_perte = GainPerteRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._liste = ListeGeneraleRepository(settings)
        self._structures = StructureRepository(settings)
        self._grades = GradeRepository(settings)
        self._sme = SmeRepository(settings)
        self._gain_perte = GainPerteRepository(settings)

    def rebind_for_archive(self, data_settings: Settings, ref_settings: Settings) -> None:
        """Mode archive : ListeGenerale depuis l'archive, référentiel depuis la base principale."""
        self._settings = ref_settings
        self._liste = ListeGeneraleRepository(data_settings)
        self._structures = StructureRepository(ref_settings)
        self._grades = GradeRepository(ref_settings)
        self._sme = SmeRepository(ref_settings)
        self._gain_perte = GainPerteRepository(ref_settings)

    def snapshot(self) -> DashboardSnapshot:
        rows = self._liste.fetch_all()
        total = len(rows)
        presents = sum(1 for r in rows if padmin_counts_as_present(r.position_admin, "present"))
        indispo = total - presents
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

        grade_keys = sorted(grade_counter.keys(), key=lambda k: (-grade_counter[k], k))
        n_grades_distinct = len(grade_keys)
        uniform_pct = (100.0 / n_grades_distinct) if n_grades_distinct else 0.0
        grades_detail: list[tuple[str, int, float, float]] = []
        for g in grade_keys:
            c = grade_counter[g]
            pct = (100.0 * c / total) if total else 0.0
            ecart = pct - uniform_pct
            grades_detail.append((g, c, pct, ecart))

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

        main_kpi = build_main_kpi_cards(total, presents, taux, rows)

        # --- Gains & Pertes du mois actif (TabGainPerte) ---
        from sme_gend.config.sme_context import load_sme_context as _ctx
        mois_actif = (_ctx().mois_actif or "").strip()
        gp_rows: tuple[GainPerteRow, ...] = ()
        gp_cards: tuple[DashboardKpiCard, ...] = ()
        try:
            if mois_actif:
                gp_list = self._gain_perte.fetch_for_month(mois_actif)
                gp_rows = tuple(gp_list)
                n_deces = sum(1 for r in gp_list if r.is_deces)
                n_gains = sum(1 for r in gp_list if r.is_gain)
                n_pertes = sum(1 for r in gp_list if r.is_perte)
                if gp_list:
                    gp_cards = build_gain_perte_kpi_cards(mois_actif, n_deces, n_gains, n_pertes)
        except Exception:
            pass

        return DashboardSnapshot(
            total_effectifs=total,
            presents=presents,
            indisponibles=indispo,
            taux_presence_pct=taux,
            main_kpi_cards=main_kpi,
            positions=positions,
            structures_distinctes_effectifs=structs_eff,
            nb_structures_referentiel=nb_struct,
            nb_grades_referentiel=nb_grades,
            nb_subdivisions_hierarchie=nb_sub,
            grades_distribution=grades_distribution,
            structures_distribution=structures_distribution,
            grades_effectifs_detail=tuple(grades_detail),
            sme_table_ok=sme_table_ok,
            sme_rows=tuple(sme_rows_list),
            sme_trend_points=tuple(trend),
            gain_perte_cards=gp_cards,
            gain_perte_rows=gp_rows,
        )
