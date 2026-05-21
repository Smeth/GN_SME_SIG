"""Comparaison des détachés entre le mois affiché et le mois précédent (archives sme_archives)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.detaches_service import DetachesService
from sme_gend.services.sme_month_closure import (
    archive_slug_candidates_for_month,
    find_archive_for_month,
    liste_generale_settings_for_archive,
    shift_sme_month_yyyy_mm,
    sme_archives_dir,
)


@dataclass(slots=True)
class DetachesMoisCompareResult:
    """Résultat archive mois précédent ↔ source liste actuelle."""

    nouveaux: list[ListeGeneraleRow]
    """Détachés dans le mois courant dont la clé n'était pas au périmètre détachés dans l'archive."""
    partis: list[ListeGeneraleRow]
    """Détachés dans l'archive du mois préc. dont la clé n'est plus au périmètre sur la source actuelle."""
    message: str
    comparable: bool
    n_prev_lignes: int = 0
    n_cur_lignes: int = 0
    n_prev_cles: int = 0
    n_cur_cles: int = 0
    cles_dupliquees_prev: bool = False
    cles_dupliquees_cur: bool = False


def nouveaux_detaches_vs_mois_precedent(
    current_liste_settings: Settings,
    mois_actif: str | None,
) -> DetachesMoisCompareResult:
    """
    Compare les **fiches** (clé ``satellite_join_key``), pas seulement l'effectif brut :
    des doublons de clé peuvent faire différer le nombre de lignes du nombre de personnes distinctes.
    """
    mois = (mois_actif or "").strip()
    if not mois:
        return DetachesMoisCompareResult(
            [], [],
            "Aucun mois actif défini dans le contexte SME — définissez-le sur le tableau de bord.",
            False,
        )

    prev = shift_sme_month_yyyy_mm(mois, delta_months=-1)
    if not prev:
        return DetachesMoisCompareResult(
            [], [],
            "Impossible de calculer le mois précédent à partir du mois actif.",
            False,
        )

    arch = find_archive_for_month(prev)
    if arch is None:
        arch_dir = sme_archives_dir()
        slug_hint = ", ".join(archive_slug_candidates_for_month(prev)) or "—"
        return DetachesMoisCompareResult(
            [], [],
            "La section compare les fiches avec une copie de base du mois "
            f"précédent (« {prev} »), pas seulement les totaux affichés sur le tableau de bord.\n\n"
            f"Dossier exploré :\n{arch_dir.resolve()}\n\n"
            f"Fichiers attendus : sme_bundle_<slug>_*.sqlite, .db, .mdb ou .accdb — "
            f"slug(s) essayé(s) pour ce mois : {slug_hint}.\n\n"
            "Ces copies sont créées à la clôture (bundle SQLite ou fichier importé) ou via "
            "« Importer une base existante ». Sans fichier dans ce dossier, aucune liste d’écarts.",
            False,
        )

    try:
        prev_settings = liste_generale_settings_for_archive(current_liste_settings, arch)
        prev_rows = DetachesService(prev_settings).fetch_detaches()
        cur_rows = DetachesService(current_liste_settings).fetch_detaches()
    except Exception as e:
        return DetachesMoisCompareResult(
            [], [],
            f"Lecture des détachés pour comparaison impossible : {e}",
            False,
        )

    cp = Counter(r.satellite_join_key() for r in prev_rows)
    cc = Counter(r.satellite_join_key() for r in cur_rows)
    prev_keys = set(cp)
    cur_keys = set(cc)
    dup_prev = any(v > 1 for v in cp.values())
    dup_cur = any(v > 1 for v in cc.values())

    prev_first: dict[str, ListeGeneraleRow] = {}
    for r in prev_rows:
        k = r.satellite_join_key()
        if k not in prev_first:
            prev_first[k] = r

    nouveaux = [r for r in cur_rows if r.satellite_join_key() not in prev_keys]
    partis = [prev_first[k] for k in prev_keys if k not in cur_keys]

    note = (
        f"Mois affiché (source actuelle) : « {mois.strip()} » ; archive de référence : « {prev} » "
        f"(« {arch.name} »). "
        f"Périmètre détachés — archive : {len(prev_rows)} ligne(s), {len(prev_keys)} clé(s) distincte(s) ; "
        f"mois actuel : {len(cur_rows)} ligne(s), {len(cur_keys)} clé(s) distincte(s). "
        f"Nouveaux : {len(nouveaux)} ; ne sont plus détachés (vs archive) : {len(partis)}."
    )
    if dup_prev or dup_cur:
        zones = []
        if dup_prev:
            zones.append("archive mois précédent")
        if dup_cur:
            zones.append("mois actuel")
        note += (
            " Attention : doublon(s) de clé Id/jointure dans : "
            f"{', '.join(zones)} — le nombre de lignes peut différer du nombre de fiches distinctes."
        )

    return DetachesMoisCompareResult(
        nouveaux=nouveaux,
        partis=partis,
        message=note,
        comparable=True,
        n_prev_lignes=len(prev_rows),
        n_cur_lignes=len(cur_rows),
        n_prev_cles=len(prev_keys),
        n_cur_cles=len(cur_keys),
        cles_dupliquees_prev=dup_prev,
        cles_dupliquees_cur=dup_cur,
    )
