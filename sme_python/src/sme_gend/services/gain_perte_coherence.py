"""Cohérence liste générale ↔ gains & pertes.

Un agent présent le mois précédent et absent du mois consulté doit figurer
en perte. S'il n'a aucune perte sur ce mois, une ligne « Non signalé » est
proposée (affichage, export) jusqu'à enregistrement du motif réel.
"""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.models.gain_perte import OBSERV_NON_SIGNALE, GainPerteRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.gain_perte_repository import cdsme_from_mois_ym


def liste_agent_key(row: ListeGeneraleRow) -> str:
    """Clé agent Access (``legacy_access_id``), vide si absente."""
    return (row.access_join_id or "").strip()


def _perte_ids_for_month(mouvements: list[GainPerteRow] | tuple[GainPerteRow, ...], mois_ym: str) -> set[str]:
    out: set[str] = set()
    for row in mouvements:
        if row.inferred or not row.is_perte:
            continue
        if row.effective_mois_ym() != mois_ym:
            continue
        key = (row.id or "").strip()
        if key:
            out.add(key)
    return out


def build_unsignaled_pertes(
    previous: list[ListeGeneraleRow],
    current: list[ListeGeneraleRow],
    mouvements: list[GainPerteRow] | tuple[GainPerteRow, ...],
    mois_ym: str,
) -> list[GainPerteRow]:
    """Pertes manquantes : présents en ``previous``, absents de ``current``.

    Retourne une liste vide si l'un des deux mois n'a pas de liste (comparaison
    impossible) ou si chaque départ a déjà une perte sur ``mois_ym``.
    """
    ym = (mois_ym or "").strip()
    if not ym or not previous or not current:
        return []
    current_keys = {liste_agent_key(r) for r in current}
    current_keys.discard("")
    if not current_keys:
        return []
    already = _perte_ids_for_month(mouvements, ym)
    cdsme = cdsme_from_mois_ym(ym)
    seen: set[str] = set()
    out: list[GainPerteRow] = []
    for row in previous:
        key = liste_agent_key(row)
        if not key or key in current_keys or key in already or key in seen:
            continue
        seen.add(key)
        out.append(
            GainPerteRow(
                id=key,
                grade=(row.grade or "").strip() or None,
                nom=(row.noms or "").strip() or None,
                objet="PERTE",
                cdsme=cdsme,
                observ=OBSERV_NON_SIGNALE,
                mois_ym=ym,
                inferred=True,
                structure_code=(row.structure or "").strip() or None,
            )
        )
    out.sort(key=lambda r: ((r.nom or "").casefold(), (r.grade or "").casefold(), r.id))
    return out


def load_unsignaled_pertes(
    settings: Settings,
    mois_ym: str,
    mouvements: list[GainPerteRow] | tuple[GainPerteRow, ...],
) -> list[GainPerteRow]:
    """Compare la liste générale du mois précédent et de ``mois_ym``."""
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    from sme_gend.services.sme_month_closure import (
        canonical_sme_mois_yyyy_mm,
        shift_sme_month_yyyy_mm,
    )

    ym = canonical_sme_mois_yyyy_mm(mois_ym) or (mois_ym or "").strip()
    prev = shift_sme_month_yyyy_mm(ym, delta_months=-1) if ym else None
    if not ym or not prev:
        return []
    repo = ListeGeneraleRepository(settings)
    current = repo.fetch_all_at_mois(ym)
    previous = repo.fetch_all_at_mois(prev)
    return build_unsignaled_pertes(previous, current, mouvements, ym)
