"""Verrouillage des éditions selon TabSme et mois actif ; réouverture exceptionnelle."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.sme_month_closure import mois_labels_equivalent

STATUT_REOUVERT = "Réouvert"


def _norm_statut(s: str) -> str:
    return (s or "").strip().casefold()


def is_statut_ferme(statut: str) -> bool:
    """Considère la période comme figée si le libellé TabSme indique une clôture."""
    t = _norm_statut(statut)
    if not t:
        return False
    if "ferm" in t:
        return True
    if t in ("clos", "closed", "clôturé", "cloture", "cloturé"):
        return True
    return False


def is_statut_editable(statut: str) -> bool:
    """Ouvert, Réouvert, ou tout statut non explicitement fermé."""
    return not is_statut_ferme(statut)


@dataclass(slots=True)
class SmeEditingState:
    locked: bool
    """Message pour bannière ou boîte de dialogue."""
    detail: str
    mois_actif: str
    tabsme_statut: str | None
    tabsme_code: str | None


def evaluate_editing_state(settings: Settings) -> SmeEditingState:
    ctx = load_sme_context()
    mois = (ctx.mois_actif or "").strip()
    if not mois:
        return SmeEditingState(
            False,
            "Aucun mois actif défini : les modifications ne sont pas rattachées à une période SME. "
            "Définissez le mois sur le tableau de bord.",
            "",
            None,
            None,
        )
    repo = SmeRepository(settings)
    if not repo.table_exists():
        return SmeEditingState(
            False,
            "Table TabSme absente : verrouillage par période désactivé.",
            mois,
            None,
            None,
        )
    match = None
    for r in repo.fetch_all():
        if mois_labels_equivalent(r.mois, mois):
            match = r
            break
    if match is None:
        return SmeEditingState(
            False,
            f'Aucune ligne TabSme pour « {mois} » : édition autorisée (vérifiez la cohérence des périodes).',
            mois,
            None,
            None,
        )
    st = (match.statut or "").strip()
    if is_statut_ferme(st):
        return SmeEditingState(
            True,
            f'Période « {mois} » clôturée (TabSme : {st}). Utilisez « Réouvrir (exception)… » sur le tableau de bord.',
            mois,
            st,
            match.codsme,
        )
    return SmeEditingState(False, "", mois, st or None, match.codsme or None)


@dataclass(slots=True)
class SmeReopenResult:
    warnings: list[str]
    rows_updated: int


def reopen_closed_month_for_corrections(
    settings: Settings,
    mois_cible: str,
    auteur: str,
    motif: str,
) -> SmeReopenResult:
    """
    Passe les lignes TabSme du mois indiqué de statut « fermé » à « Réouvert ».
    Enregistre un événement d'audit applicatif.
    """
    warnings: list[str] = []
    auteur_s = (auteur or "").strip()
    motif_s = (motif or "").strip()
    if not auteur_s or not motif_s:
        return SmeReopenResult(
            warnings=["Indiquez l'auteur (ou le poste) et le motif de la réouverture."],
            rows_updated=0,
        )

    repo = SmeRepository(settings)
    if not repo.table_exists():
        return SmeReopenResult(
            warnings=["Table TabSme absente ou inaccessible."],
            rows_updated=0,
        )

    rows = repo.fetch_all()
    n = 0
    for r in rows:
        if mois_labels_equivalent(r.mois, mois_cible) and is_statut_ferme(r.statut):
            repo.update(r.codsme, r.mois, STATUT_REOUVERT)
            n += 1

    if n == 0:
        warnings.append(
            f'Aucune ligne TabSme « fermée » trouvée pour « {mois_cible} » '
            "(vérifiez le mois ou le libellé de statut dans Access / SQLite)."
        )

    ctx = load_sme_context()
    ym_ctx = (ctx.mois_actif or "").strip() or None
    append_audit_event(
        action="sme_reopen_exception",
        sme_mois_context=ym_ctx,
        mois_cible=mois_cible,
        auteur=auteur_s,
        motif=motif_s,
        tabsme_rows_reopened=n,
        warnings=warnings,
    )
    return SmeReopenResult(warnings=warnings, rows_updated=n)
