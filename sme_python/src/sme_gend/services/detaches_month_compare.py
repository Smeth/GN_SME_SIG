"""Comparaison des détachés entre deux mois SME (snapshots MySQL / liste générale)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.detaches_service import DetachesService, sigle_ou_subdivision
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm, shift_sme_month_yyyy_mm


@dataclass(slots=True)
class DetachesMoisCompareResult:
    """Comparaison périmètre détachés entre un mois de référence et un mois cible."""

    nouveaux: list[ListeGeneraleRow]
    """Présents au périmètre détachés en mois cible, absents en mois référence."""
    partis: list[ListeGeneraleRow]
    """Présents en mois référence, absents du périmètre en mois cible."""
    modifies: list[tuple[ListeGeneraleRow, ListeGeneraleRow]]
    """Même fiche détachée aux deux mois, mais sigle / structure / Padmin différents."""
    message: str
    comparable: bool
    mois_ref: str = ""
    mois_cible: str = ""
    n_prev_lignes: int = 0
    n_cur_lignes: int = 0
    n_prev_cles: int = 0
    n_cur_cles: int = 0
    cles_dupliquees_prev: bool = False
    cles_dupliquees_cur: bool = False


def _settings_for_mois(base: Settings, mois_ym: str) -> Settings:
    ym = canonical_sme_mois_yyyy_mm(mois_ym) or (mois_ym or "").strip()
    return replace(base, mysql_view_mois=ym)


def _compare_signature(row: ListeGeneraleRow) -> tuple[str, str, str]:
    return (
        (row.position_admin or "").strip().casefold(),
        (row.structure or "").strip().casefold(),
        sigle_ou_subdivision(row).casefold(),
    )


def compare_detaches_entre_mois(
    settings: Settings,
    mois_ref: str,
    mois_cible: str,
) -> DetachesMoisCompareResult:
    """
    Compare les fiches (clé ``satellite_join_key``) entre deux mois SME en base MySQL.
    """
    ref_ym = canonical_sme_mois_yyyy_mm(mois_ref) or (mois_ref or "").strip()
    cur_ym = canonical_sme_mois_yyyy_mm(mois_cible) or (mois_cible or "").strip()
    if not ref_ym or not cur_ym:
        return DetachesMoisCompareResult(
            [],
            [],
            [],
            "Indiquez deux mois SME valides (YYYY-MM).",
            False,
        )
    if ref_ym == cur_ym:
        return DetachesMoisCompareResult(
            [],
            [],
            [],
            "Choisissez deux mois distincts pour la comparaison.",
            False,
            mois_ref=ref_ym,
            mois_cible=cur_ym,
        )

    try:
        ref_rows = DetachesService(_settings_for_mois(settings, ref_ym)).fetch_detaches()
        cur_rows = DetachesService(_settings_for_mois(settings, cur_ym)).fetch_detaches()
    except Exception as e:
        return DetachesMoisCompareResult(
            [],
            [],
            [],
            f"Lecture des détachés impossible : {e}",
            False,
            mois_ref=ref_ym,
            mois_cible=cur_ym,
        )

    cp = Counter(r.satellite_join_key() for r in ref_rows)
    cc = Counter(r.satellite_join_key() for r in cur_rows)
    ref_keys = set(cp)
    cur_keys = set(cc)
    dup_ref = any(v > 1 for v in cp.values())
    dup_cur = any(v > 1 for v in cc.values())

    ref_first: dict[str, ListeGeneraleRow] = {}
    for r in ref_rows:
        k = r.satellite_join_key()
        if k not in ref_first:
            ref_first[k] = r
    cur_first: dict[str, ListeGeneraleRow] = {}
    for r in cur_rows:
        k = r.satellite_join_key()
        if k not in cur_first:
            cur_first[k] = r

    nouveaux = [r for r in cur_rows if r.satellite_join_key() not in ref_keys]
    partis = [ref_first[k] for k in ref_keys if k not in cur_keys]
    modifies: list[tuple[ListeGeneraleRow, ListeGeneraleRow]] = []
    for k in ref_keys & cur_keys:
        pr, cr = ref_first[k], cur_first[k]
        if _compare_signature(pr) != _compare_signature(cr):
            modifies.append((pr, cr))

    delta_l = len(cur_rows) - len(ref_rows)
    delta_k = len(cur_keys) - len(ref_keys)

    def _evol(delta: int, label: str) -> str:
        if delta > 0:
            return f"augmentation de {delta} {label}"
        if delta < 0:
            return f"diminution de {-delta} {label}"
        return "stable (même effectif)"

    note = (
        f"Du mois {ref_ym} au mois {cur_ym} : "
        f"{len(ref_rows)} détaché(s), puis {len(cur_rows)} "
        f"— {_evol(delta_l, 'détaché(s)')} au total, "
        f"{_evol(delta_k, 'personne(s) distincte(s)')}. "
        f"{len(nouveaux)} personne(s) deviennent détachée(s), "
        f"{len(partis)} ne le sont plus, "
        f"{len(modifies)} restent détachées avec des informations modifiées "
        "(sigle, structure ou position admin.)."
    )
    if dup_ref or dup_cur:
        zones = []
        if dup_ref:
            zones.append(ref_ym)
        if dup_cur:
            zones.append(cur_ym)
        note += (
            " Attention : plusieurs lignes pour la même personne en "
            f"{', '.join(zones)} — vérifiez les identifiants en base."
        )

    return DetachesMoisCompareResult(
        nouveaux=nouveaux,
        partis=partis,
        modifies=modifies,
        message=note,
        comparable=True,
        mois_ref=ref_ym,
        mois_cible=cur_ym,
        n_prev_lignes=len(ref_rows),
        n_cur_lignes=len(cur_rows),
        n_prev_cles=len(ref_keys),
        n_cur_cles=len(cur_keys),
        cles_dupliquees_prev=dup_ref,
        cles_dupliquees_cur=dup_cur,
    )


def default_compare_months(settings: Settings) -> tuple[str, str]:
    """Mois cible = liste consultée ; référence = mois SME précédent si disponible."""
    ctx = load_sme_context()
    cible = (
        resolve_liste_view_mois(settings)
        or canonical_sme_mois_yyyy_mm(ctx.mois_actif or "")
        or ""
    )
    ref = shift_sme_month_yyyy_mm(cible, delta_months=-1) if cible else None
    return (ref or "", cible or "")


def nouveaux_detaches_vs_mois_precedent(
    current_liste_settings: Settings,
    mois_actif: str | None,
) -> DetachesMoisCompareResult:
    """Compatibilité : compare mois actif / consulté au mois SME précédent (MySQL)."""
    mois = (mois_actif or "").strip()
    if not mois:
        ref, cible = default_compare_months(current_liste_settings)
        if not cible:
            return DetachesMoisCompareResult(
                [],
                [],
                [],
                "Aucun mois SME défini — configurez le contexte (Gestion des mois et archives).",
                False,
            )
        if not ref:
            return DetachesMoisCompareResult(
                [],
                [],
                [],
                "Impossible de calculer le mois précédent pour la comparaison.",
                False,
                mois_cible=cible,
            )
        return compare_detaches_entre_mois(current_liste_settings, ref, cible)

    prev = shift_sme_month_yyyy_mm(mois, delta_months=-1)
    if not prev:
        return DetachesMoisCompareResult(
            [],
            [],
            [],
            "Impossible de calculer le mois précédent à partir du mois actif.",
            False,
        )
    return compare_detaches_entre_mois(current_liste_settings, prev, mois)
