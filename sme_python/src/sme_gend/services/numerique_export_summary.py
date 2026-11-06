"""Résumé pré-export du classeur situation numérique."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.config.liste_generale_scope import (
    build_export_mois_context,
    settings_for_chosen_export_mois,
    settings_for_numerique_data,
)
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.liste_generale_cache import cached_numerique_liste_rows
from sme_gend.services.numerique_excel_export import sorted_rows_for_padmin
from sme_gend.services.numerique_padmin import (
    PADMIN_DESERTEUR,
    PADMIN_MALADE,
    PADMIN_MISSION,
    PADMIN_STAGE_EX,
    PADMIN_STAGE_LOCAL,
)


@dataclass(frozen=True, slots=True)
class NumeriqueExportSummary:
    export_mois: str | None
    mois_actif: str | None
    is_archive_view: bool
    agent_count: int
    missions: int
    malades: int
    deserteurs: int
    stages_loc: int
    stages_ex: int
    detaches: int = 0

    @property
    def satellites_total(self) -> int:
        return self.missions + self.malades + self.deserteurs + self.stages_loc + self.stages_ex


def _count_padmin_rows(rows: list[ListeGeneraleRow], padmin_code: str) -> int:
    """Même filtre que les feuilles MISSION / MALADE / … de l'export Excel."""
    return len(sorted_rows_for_padmin(rows, padmin_code))


def build_numerique_export_summary(
    settings: Settings,
    *,
    export_mois: str | None = None,
) -> NumeriqueExportSummary:
    data_settings = settings_for_numerique_data(
        settings_for_chosen_export_mois(settings, export_mois)
    )
    ctx = build_export_mois_context(data_settings)
    rows = cached_numerique_liste_rows(data_settings)
    detaches = 0
    try:
        from sme_gend.services.detaches_service import DetachesService

        detaches = len(DetachesService(data_settings).fetch_detaches())
    except Exception:
        detaches = 0
    return NumeriqueExportSummary(
        export_mois=ctx.export_mois,
        mois_actif=ctx.mois_actif,
        is_archive_view=ctx.is_archive_view,
        agent_count=len(rows),
        missions=_count_padmin_rows(rows, PADMIN_MISSION),
        malades=_count_padmin_rows(rows, PADMIN_MALADE),
        deserteurs=_count_padmin_rows(rows, PADMIN_DESERTEUR),
        stages_loc=_count_padmin_rows(rows, PADMIN_STAGE_LOCAL),
        stages_ex=_count_padmin_rows(rows, PADMIN_STAGE_EX),
        detaches=detaches,
    )


def format_numerique_export_preview_text(summary: NumeriqueExportSummary) -> str:
    mois = summary.export_mois or "—"
    lines = [
        f"Mois SME des données : {mois}",
        f"Agents (liste générale) : {summary.agent_count}",
        f"Détachés : {summary.detaches}",
        "",
        "Feuilles satellites (position administrative, comme l’aperçu Listes détaillées) :",
        f"  • Missions : {summary.missions}",
        f"  • Malades : {summary.malades}",
        f"  • Désertions : {summary.deserteurs}",
        f"  • Stages locaux : {summary.stages_loc}",
        f"  • Stages extérieurs : {summary.stages_ex}",
        f"  — Total satellites : {summary.satellites_total}",
        "",
        "Feuilles incluses : LISTE GENERALE, LISTE DES PRÉSENTS, NUMERIQUE,",
        "GAIN PERTE, INDISPO, DETACHE, DETACHE PAR STRUCTURE,",
        "MISSION, MALADE, DESERTEUR, STAGE-LOC, STAGE-EXT,",
        "CONGÉ ADMIN, DETENTION-PRISON, NON-REJOINT.",
    ]
    if summary.is_archive_view and summary.mois_actif and summary.export_mois != summary.mois_actif:
        lines.insert(
            1,
            f"Mois actif de session : {summary.mois_actif} (archive consultée)",
        )
    return "\n".join(lines)
