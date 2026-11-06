"""Règles d'alerte congés > 30 jours."""

from __future__ import annotations

from datetime import date

from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.conge_duration_alerts import (
    build_conge_movement_rows,
    is_long_duration_conge,
    conge_is_ongoing_at,
)


def _liste_row(padmin: str = "CongéAdmin") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="a1",
        noms="TEST",
        date_naissance=None,
        lieu_naissance=None,
        grade="GND",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=padmin,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U1",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_long_conge_after_30_days():
    ref = date(2026, 3, 1)
    conge = AgentCongeRow(
        type_conge="ADMIN",
        type_label="Congé administratif",
        date_debut="01/01/2026",
        date_fin="31/03/2026",
        quota_jours="90",
        observation=None,
        periode_sme="2026-03",
        source_code=None,
        id=1,
    )
    assert conge_is_ongoing_at(conge, ref)
    assert is_long_duration_conge(conge, ref, min_days=30)
    assert not is_long_duration_conge(conge, date(2026, 1, 15), min_days=30)


def test_finished_conge_not_alert():
    ref = date(2026, 3, 1)
    conge = AgentCongeRow(
        type_conge="ADMIN",
        type_label="Congé administratif",
        date_debut="01/01/2026",
        date_fin="15/02/2026",
        quota_jours="46",
        observation=None,
        periode_sme="2026-03",
        source_code=None,
        id=1,
    )
    assert not conge_is_ongoing_at(conge, ref)
    assert not is_long_duration_conge(conge, ref)


def test_build_rows_flags_alert():
    from unittest.mock import MagicMock

    settings = MagicMock()
    row = _liste_row()
    conge = AgentCongeRow(
        type_conge="ADMIN",
        type_label="Congé administratif",
        date_debut="01/01/2020",
        date_fin=None,
        quota_jours=None,
        observation=None,
        periode_sme="2026-03",
        source_code=None,
        id=1,
    )
    conges = {"a1": conge}

    class _FixedRef:
        pass

    import sme_gend.services.conge_duration_alerts as mod

    original = mod.reference_date_for_conge_alert
    mod.reference_date_for_conge_alert = lambda _s: date(2026, 2, 15)
    try:
        built = build_conge_movement_rows([row], conges, settings)
    finally:
        mod.reference_date_for_conge_alert = original

    assert len(built) == 1
    assert built[0].is_long_alert is True
