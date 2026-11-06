"""Verrouillage des éditions selon sme_periode et mois actif ; réouverture exceptionnelle."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.sme_month_closure import extract_yyyy_mm, mois_labels_equivalent

STATUT_REOUVERT = "Réouvert"


def _norm_statut(s: str) -> str:
    return (s or "").strip().casefold()


def is_statut_ferme(statut: str) -> bool:
    """Considère la période comme figée si le libellé sme_periode indique une clôture."""
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


_edit_state_cache: dict[tuple[str, str], SmeEditingState] = {}
# Incrémenté à chaque invalidation — permet à evaluate_editing_state() de détecter
# qu'une invalidation a eu lieu pendant son propre calcul (ex. lancé en arrière-plan
# via run_in_background) et de ne pas réécrire un résultat périmé dans le cache
# après une clôture/réouverture qui vient de l'invalider.
_edit_state_epoch = 0


def _settings_cache_key(settings: Settings) -> str:
    s = settings
    return f"{s.mysql_host}:{s.mysql_port}:{s.mysql_database}"


def invalidate_editing_state_cache() -> None:
    global _edit_state_epoch
    _edit_state_cache.clear()
    _edit_state_epoch += 1


def peek_editing_state(
    settings: Settings,
    *,
    view_mois: str | None = None,
) -> SmeEditingState | None:
    """État en cache sans requête MySQL (None si absent)."""
    ctx = load_sme_context()
    mois = (view_mois or ctx.mois_actif or "").strip()
    if not mois:
        return None
    return _edit_state_cache.get((_settings_cache_key(settings), mois))


def evaluate_editing_state_for_settings(settings: Settings) -> SmeEditingState:
    """Verrouillage aligné sur le mois affiché (consultation ou travail)."""
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois

    vm = resolve_liste_view_mois(settings)
    return evaluate_editing_state(settings, view_mois=vm)


def evaluate_editing_state(
    settings: Settings,
    *,
    view_mois: str | None = None,
) -> SmeEditingState:
    """
    Évalue le verrouillage de saisie pour un mois affiché.

    ``view_mois`` : mois consulté (archive) ; par défaut le mois actif opérationnel.
    """
    ctx = load_sme_context()
    mois = (view_mois or ctx.mois_actif or "").strip()
    cache_key = (_settings_cache_key(settings), mois)
    if mois and cache_key in _edit_state_cache:
        return _edit_state_cache[cache_key]
    if not mois:
        return SmeEditingState(
            False,
            "Aucun mois actif défini : les modifications ne sont pas rattachées à une période SME. "
            "Définissez le mois actif (Piloter le mois SME → Gestion des mois et archives).",
            "",
            None,
            None,
        )

    epoch_at_start = _edit_state_epoch

    def _store(st: SmeEditingState) -> SmeEditingState:
        # N'écrit dans le cache que si aucune invalidation n'a eu lieu depuis le
        # début de ce calcul — sinon ce résultat est potentiellement périmé
        # (calculé sur l'état d'avant une clôture/réouverture concurrente).
        if _edit_state_epoch == epoch_at_start:
            _edit_state_cache[cache_key] = st
        return st

    repo = SmeRepository(settings)
    if not repo.table_exists():
        return _store(
            SmeEditingState(
                False,
                "Table sme_periode absente : verrouillage par période désactivé.",
                mois,
                None,
                None,
            )
        )
    match = None
    for r in repo.fetch_all():
        if mois_labels_equivalent(r.mois, mois):
            match = r
            break
    if match is None:
        return _store(
            SmeEditingState(
                False,
                f'Aucune ligne sme_periode pour « {mois} » : édition autorisée (vérifiez la cohérence des périodes).',
                mois,
                None,
                None,
            )
        )
    st_label = (match.statut or "").strip()
    if is_statut_ferme(st_label):
        st = SmeEditingState(
            True,
            f'Période « {mois} » clôturée (sme_periode : {st_label}). Utilisez « Réouvrir (exception)… » (Gestion des mois et archives).',
            mois,
            st_label,
            match.codsme,
        )
    else:
        st = SmeEditingState(False, "", mois, st_label or None, match.codsme or None)
    return _store(st)


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
    Passe les lignes sme_periode du mois indiqué de statut « fermé » à « Réouvert ».
    Enregistre un événement d'audit applicatif.
    """
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("workflow.reopen_month")
    warnings: list[str] = []
    auteur_s = (auteur or "").strip()
    motif_s = (motif or "").strip()
    if not auteur_s or not motif_s:
        return SmeReopenResult(
            warnings=["Indiquez l'auteur (ou le poste) et le motif de la réouverture."],
            rows_updated=0,
        )

    from sme_gend.services.active_month_guard import period_state_write
    with period_state_write(settings,reason="month_reopened",mois=mois_cible):
        repo = SmeRepository(settings)
        if not repo.table_exists():
            return SmeReopenResult(
                warnings=["Table sme_periode absente ou inaccessible."],
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
                f'Aucune ligne sme_periode « fermée » trouvée pour « {mois_cible} » '
                "(vérifiez le mois ou le libellé de statut dans sme_periode / sme_periode)."
            )

        ym = extract_yyyy_mm(mois_cible) or mois_cible.strip()
        if ym and n > 0:
            try:
                from sme_gend.repositories.mysql_connection import MySQLConnection
                from sme_gend.services.mysql_import_period import ensure_sme_periode

                mc = MySQLConnection(settings)
                with mc.connect() as conn:
                    ensure_sme_periode(conn, ym, statut=STATUT_REOUVERT)
            except Exception as e:
                raise RuntimeError("Réouverture annulée : mise à jour des périodes impossible.") from e

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
