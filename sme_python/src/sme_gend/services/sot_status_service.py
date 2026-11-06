"""État SoT pour l'UI : écarts dossier ↔ liste et FK carrière non résolues."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.edition_sme_support import fetch_dossier_gap_carriere_ids
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.services.sot_maintenance import collect_sot_metrics


@dataclass(slots=True)
class SotStatusSummary:
    mois: str | None
    gap_ids: set[str]
    fk_unresolved_ids: set[str]
    ecarts_diplome: int = 0
    ecarts_grade: int = 0
    ecarts_affectation: int = 0
    fk_grade: int = 0
    fk_structure: int = 0
    fk_padmin: int = 0
    fk_fonction: int = 0
    fk_subdiv: int = 0

    @property
    def fk_total(self) -> int:
        return (
            self.fk_grade
            + self.fk_structure
            + self.fk_padmin
            + self.fk_fonction
            + self.fk_subdiv
        )

    @property
    def has_issues(self) -> bool:
        return bool(self.gap_ids or self.fk_unresolved_ids)


def _view_mois(settings: Settings) -> str | None:
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois

    return resolve_liste_view_mois(settings)


def fetch_fk_unresolved_carriere_ids(settings: Settings) -> set[str]:
    """Identifiants carrière du mois consulté avec au moins une FK manquante."""
    from sme_gend.config.liste_generale_scope import carriere_scope_for_settings

    scope_sql, scope_params = carriere_scope_for_settings(settings, alias="c")
    ids: set[str] = set()
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id FROM carriere_situation c
                WHERE {scope_sql}
                  AND (
                    (c.grade_id IS NULL AND TRIM(COALESCE(c.grade_libelle, '')) <> '')
                    OR (c.structure_id IS NULL AND TRIM(COALESCE(c.structure_code, '')) <> '')
                    OR (c.position_admin_id IS NULL
                        AND TRIM(COALESCE(c.position_admin_libelle, '')) <> '')
                    OR (c.fonction_id IS NULL AND TRIM(COALESCE(c.fonction_libelle, '')) <> '')
                    OR (c.subdivision_arme_id IS NULL
                        AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> '')
                  )
                """,
                scope_params,
            )
            ids.update(str(r[0]) for r in cur.fetchall())
    return ids


def fetch_row_quality_ids(settings: Settings) -> tuple[set[str], set[str]]:
    """``(valeurs différentes du dossier, valeurs hors référentiels)``.

    Version légère pour la coloration des lignes : deux requêtes indexées, sans
    les métriques agrégées sur 12 mois, donc supportable à chaque chargement.
    """
    gap_ids = fetch_dossier_gap_carriere_ids(settings)
    fk_ids = fetch_fk_unresolved_carriere_ids(settings)
    return gap_ids, fk_ids


def fetch_sot_status_summary(settings: Settings) -> SotStatusSummary:
    """Résumé pour bandeaux liste / pilotage (requêtes légères + métriques agrégées)."""
    mois = _view_mois(settings) or canonical_sme_mois_yyyy_mm(
        load_sme_context().mois_actif or ""
    )
    gap_ids: set[str] = set()
    fk_ids: set[str] = set()
    try:
        gap_ids = fetch_dossier_gap_carriere_ids(settings)
    except Exception:
        pass
    try:
        fk_ids = fetch_fk_unresolved_carriere_ids(settings)
    except Exception:
        pass

    summary = SotStatusSummary(
        mois=mois or None,
        gap_ids=gap_ids,
        fk_unresolved_ids=fk_ids,
    )
    try:
        metrics = collect_sot_metrics(settings, mois=mois or None)
        summary.ecarts_diplome = metrics.ecarts_diplome
        summary.ecarts_grade = metrics.ecarts_grade
        summary.ecarts_affectation = metrics.ecarts_affectation
        if metrics.unresolved_actif is not None:
            u = metrics.unresolved_actif
            summary.fk_grade = u.grade
            summary.fk_structure = u.structure
            summary.fk_padmin = u.position_admin
            summary.fk_fonction = u.fonction
            summary.fk_subdiv = u.subdivision_arme
    except Exception:
        pass
    return summary
