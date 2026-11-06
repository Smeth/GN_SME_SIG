"""Normalisation des instantanés tableau de bord (évite AttributeError UI / versions mixtes)."""

from __future__ import annotations

from typing import Any

from sme_gend.services.dashboard_service import DashboardKpiCard, DashboardSnapshot


def _tuple_or_empty(val: Any) -> tuple:
    if val is None:
        return ()
    if isinstance(val, tuple):
        return val
    return tuple(val)


def _list_or_empty(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return list(val)


def coerce_dashboard_snapshot(s: Any) -> DashboardSnapshot:
    """
    Retourne un ``DashboardSnapshot`` complet.

    Accepte un snapshot déjà typé ou un objet partiel (tests, cache inter-versions).
    """
    if isinstance(s, DashboardSnapshot):
        return s
    return DashboardSnapshot(
        total_effectifs=int(getattr(s, "total_effectifs", 0) or 0),
        presents=int(getattr(s, "presents", 0) or 0),
        indisponibles=int(getattr(s, "indisponibles", 0) or 0),
        taux_presence_pct=float(getattr(s, "taux_presence_pct", 0.0) or 0.0),
        main_kpi_cards=_tuple_or_empty(getattr(s, "main_kpi_cards", ())),
        positions=_list_or_empty(getattr(s, "positions", [])),
        structures_distinctes_effectifs=int(
            getattr(s, "structures_distinctes_effectifs", 0) or 0
        ),
        nb_structures_referentiel=int(
            getattr(s, "nb_structures_referentiel", 0) or 0
        ),
        nb_grades_referentiel=int(getattr(s, "nb_grades_referentiel", 0) or 0),
        nb_subdivisions_hierarchie=int(
            getattr(s, "nb_subdivisions_hierarchie", 0) or 0
        ),
        grades_distribution=_list_or_empty(getattr(s, "grades_distribution", [])),
        structures_distribution=_list_or_empty(
            getattr(s, "structures_distribution", [])
        ),
        grades_effectifs_detail=_tuple_or_empty(
            getattr(s, "grades_effectifs_detail", ())
        ),
        sme_table_ok=bool(getattr(s, "sme_table_ok", False)),
        sme_rows=_tuple_or_empty(getattr(s, "sme_rows", ())),
        sme_trend_points=_tuple_or_empty(getattr(s, "sme_trend_points", ())),
        gain_perte_cards=_tuple_or_empty(getattr(s, "gain_perte_cards", ())),
        gain_perte_rows=_tuple_or_empty(getattr(s, "gain_perte_rows", ())),
        gain_perte_mois=getattr(s, "gain_perte_mois", None),
        view_mois=getattr(s, "view_mois", None),
        is_archive_view=bool(getattr(s, "is_archive_view", False)),
    )


def snapshot_kpi_cards(s: Any) -> tuple[DashboardKpiCard, ...]:
    return _tuple_or_empty(getattr(s, "main_kpi_cards", ()))  # type: ignore[return-value]
