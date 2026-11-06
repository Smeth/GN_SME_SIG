"""Baseline « mois figé » du récap indisponibles — calcul depuis MySQL (mois SME M-1)."""

from __future__ import annotations

import logging
from dataclasses import replace

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.numerique_excel_export import _prev_ym_canon
from sme_gend.services.numerique_stats import (
    RECAP_INDISPO_KEYS,
    build_numerique_matrix,
    matrix_row_total,
)
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

_RECAP_LEN = 10  # 9 rubriques + total
_LOGGER = logging.getLogger(__name__)


def recap_indispo_totals_from_rows(
    rows: list[ListeGeneraleRow],
    grades: list[str],
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> list[int]:
    """Totaux par rubrique (RECAP_INDISPO_KEYS) + ligne TOTAL, comme colonnes CD/CE."""
    matrix = build_numerique_matrix(
        rows, grades, grade_abbrev_map=grade_abbrev_map
    )
    lines = [int(matrix_row_total(matrix, key, grades)) for key, _ in RECAP_INDISPO_KEYS]
    lines.append(sum(lines))
    return lines


def _sme_period_exists(settings: Settings, ym_canon: str) -> bool:
    if not ym_canon:
        return False
    repo = SmeRepository(settings)
    if not repo.table_exists():
        return True
    for row in repo.fetch_all():
        if canonical_sme_mois_yyyy_mm(row.mois or "") == ym_canon:
            return True
    return False


def resolve_recap_cd_baseline(
    settings: Settings,
    mois_actif: str | None,
    grades: list[str],
    *,
    grade_abbrev_map: dict[str, str] | None = None,
) -> list[int | None]:
    """
    Valeurs « mois figé » (colonne CD) pour le récap variation.

  - Mois figé = effectifs du mois SME précédent (carriere_situation sur ``annee_mois`` M-1).
  - Si le mois précédent n'existe pas en ``sme_periode`` : ``None`` (variation non calculée).
  - Si la période existe mais sans lignes importées : zéros.
    """
    if not grades:
        return [None] * _RECAP_LEN
    ym = canonical_sme_mois_yyyy_mm((mois_actif or "").strip())
    if not ym:
        return [None] * _RECAP_LEN
    prev = _prev_ym_canon(ym)
    if not _sme_period_exists(settings, prev):
        return [None] * _RECAP_LEN
    prev_settings = replace(settings, mysql_view_mois=prev)
    try:
        rows = ListeGeneraleRepository(prev_settings).fetch_all_at_mois(prev)
    except Exception:
        _LOGGER.exception("Impossible de charger la liste du mois précédent %s.", prev)
        raise
    if not rows:
        return [0] * _RECAP_LEN
    return recap_indispo_totals_from_rows(
        rows, grades, grade_abbrev_map=grade_abbrev_map
    )
