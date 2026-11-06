"""Tests gains & pertes — classification décès et mois effectif."""

from __future__ import annotations

from sme_gend.models.gain_perte import (
    GainPerteRow,
    cdsme_to_mois_ym,
    observ_is_deces,
    observ_is_non_signale,
    periode_ym_for_mouvement,
)
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.gain_perte_coherence import build_unsignaled_pertes
from sme_gend.services.mysql_access_numerique_import import _movement_cdsme_matches_import
from sme_gend.ui.views.gain_perte_view import _years_from_catalog


def test_observ_is_deces_variants() -> None:
    assert observ_is_deces("DECES")
    assert observ_is_deces("DECEDE")
    assert observ_is_deces("deces")
    assert observ_is_deces("DECEDE EN SERVICE")
    assert not observ_is_deces("RETRAITE")
    assert not observ_is_deces("Non signalé")
    assert not observ_is_deces(None)


def test_observ_is_non_signale_variants() -> None:
    assert observ_is_non_signale("Non signalé")
    assert observ_is_non_signale("NON SIGNALE")
    assert observ_is_non_signale("Non Signaler")
    assert not observ_is_non_signale("DECES")
    assert not observ_is_non_signale(None)


def test_gain_perte_row_is_deces() -> None:
    row = GainPerteRow(
        id="1",
        grade="CCH",
        nom="TEST",
        objet="PERTE",
        cdsme="012026",
        observ="DECEDE",
    )
    assert row.is_deces
    assert not GainPerteRow("2", None, None, "GAIN", "012026", "AJOUT").is_deces


def test_periode_ym_for_mouvement_prefers_mois_ym() -> None:
    row = GainPerteRow(
        id="3",
        grade=None,
        nom=None,
        objet="PERTE",
        cdsme="012026",
        observ="RETRAITE",
        mois_ym="2026-05",
    )
    assert periode_ym_for_mouvement(row) == "2026-05"


def test_effective_mois_ym_prefers_sme_periode() -> None:
    row = GainPerteRow(
        id="3",
        grade=None,
        nom=None,
        objet="PERTE",
        cdsme="012026",
        observ="RETRAITE",
        mois_ym="2026-05",
    )
    assert row.effective_mois_ym() == "2026-05"


def test_effective_mois_ym_fallback_cdsme() -> None:
    row = GainPerteRow(
        id="4",
        grade=None,
        nom=None,
        objet="GAIN",
        cdsme="052026",
        observ="AJOUT",
    )
    assert row.effective_mois_ym() == "2026-05"
    assert cdsme_to_mois_ym("012026") == "2026-01"


def test_access_movements_are_scoped_to_the_imported_month() -> None:
    assert _movement_cdsme_matches_import("092026", "2026-09")
    assert not _movement_cdsme_matches_import("082026", "2026-09")
    assert not _movement_cdsme_matches_import("132026", "2026-09")
    assert not _movement_cdsme_matches_import("09202", "2026-09")
    assert not _movement_cdsme_matches_import(None, "2026-09")
    assert not _movement_cdsme_matches_import("", "2026-09")


def test_years_from_catalog() -> None:
    assert _years_from_catalog(["2026-05", "2026-01", "2025-12"]) == [2026, 2025]


def _liste(aid: str, nom: str, *, grade: str = "ADC", structure: str = "RG-BZA") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms=nom,
        date_naissance=None,
        lieu_naissance=None,
        grade=grade,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=structure,
        obs=None,
        ordre_tri=None,
        presseance=None,
        access_join_id=aid,
    )


def _perte(aid: str, mois: str, observ: str = "DECES") -> GainPerteRow:
    return GainPerteRow(
        id=aid,
        grade="ADC",
        nom="X",
        objet="PERTE",
        cdsme="082026",
        observ=observ,
        mois_ym=mois,
    )


def test_unsignaled_perte_when_agent_leaves_without_mouvement() -> None:
    prev = [_liste("25393", "OKOGNA Landry Rodrigue"), _liste("1", "PRESENT")]
    cur = [_liste("1", "PRESENT")]
    rows = build_unsignaled_pertes(prev, cur, [], "2026-08")
    assert len(rows) == 1
    assert rows[0].id == "25393"
    assert rows[0].is_perte
    assert rows[0].inferred
    assert rows[0].observ == "Non signalé"
    assert rows[0].structure_code == "RG-BZA"
    assert rows[0].mois_ym == "2026-08"
    assert rows[0].cdsme == "082026"


def test_unsignaled_skipped_when_perte_already_recorded() -> None:
    prev = [_liste("25393", "OKOGNA Landry Rodrigue")]
    cur = [_liste("9", "AUTRE")]
    rows = build_unsignaled_pertes(prev, cur, [_perte("25393", "2026-08")], "2026-08")
    assert rows == []


def test_unsignaled_keeps_gap_if_perte_is_another_month() -> None:
    prev = [_liste("25393", "OKOGNA Landry Rodrigue")]
    cur = [_liste("9", "AUTRE")]
    rows = build_unsignaled_pertes(prev, cur, [_perte("25393", "2026-01")], "2026-08")
    assert len(rows) == 1
    assert rows[0].observ == "Non signalé"


def test_unsignaled_empty_when_a_month_has_no_liste() -> None:
    agent = _liste("25393", "OKOGNA Landry Rodrigue")
    assert build_unsignaled_pertes([agent], [], [], "2026-08") == []
    assert build_unsignaled_pertes([], [agent], [], "2026-08") == []
