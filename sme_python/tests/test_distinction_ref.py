"""Modèles médailles / décorations."""

from __future__ import annotations

from datetime import date

from sme_gend.models.distinction_ref import (
    DISTINCTION_KIND_MEDAILLE,
    AgentDistinctionRow,
    DistinctionRefRow,
)


def test_distinction_ref_display_label():
    r = DistinctionRefRow(id=1, code="LH", libelle_court="Légion d'honneur")
    assert r.display_label() == "Légion d'honneur"


def test_agent_distinction_from_sequence():
    row = AgentDistinctionRow.from_sequence(
        (
            5,
            12,
            DISTINCTION_KIND_MEDAILLE,
            3,
            None,
            "Médaille militaire",
            date(2020, 1, 15),
            None,
            "AR-1",
            None,
            "Citation test",
            "saisie_dossier",
        )
    )
    assert row.kind == DISTINCTION_KIND_MEDAILLE
    assert row.ref_medaille_id == 3
    assert row.libelle == "Médaille militaire"
