"""Tests recherche personnel étendue (effectif + hors effectif)."""

from __future__ import annotations

from unittest.mock import MagicMock

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import (
    HistoricalDossierContext,
    PersonnelSearchHit,
    SortieSummary,
)
from sme_gend.services.personnel_lookup_service import _needle_match
from sme_gend.services.personnel_lookup_service import PersonnelLookupService


def test_needle_match_min_length() -> None:
    assert not _needle_match("a", "SEFFO Jean")
    assert _needle_match("SE", "SEFFO Jean", "25496")


def test_sortie_summary_label_with_mois() -> None:
    s = SortieSummary(
        categorie="PERTE",
        motif="",
        observ="DECES",
        annee_mois="2026-04",
    )
    assert s.label() == "DECES (2026-04)"


def test_sortie_summary_label_motif_fallback() -> None:
    s = SortieSummary(
        categorie="PERTE",
        motif="REMIS AU MINISTERE DE LA DEFENSE",
        observ="",
        annee_mois="2026-04",
    )
    assert "REMIS AU MINISTERE" in s.label()


def test_historical_context_banner() -> None:
    ctx = HistoricalDossierContext(
        active_mois="2026-05",
        last_carriere_mois="2026-04",
        sortie=SortieSummary("PERTE", "", "DECES", "2026-04"),
    )
    text = ctx.banner_text()
    assert "2026-05" in text
    assert "2026-04" in text
    assert "DECES" in text
    assert "Consultation seule" in text


def test_search_hit_labels() -> None:
    row = ListeGeneraleRow(
        id="1",
        noms="SEFFO",
        date_naissance=None,
        lieu_naissance=None,
        grade="COL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire="25496",
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
        access_join_id="25496",
    )
    active = PersonnelSearchHit(kind="active", row=row, active_mois_label="2026-05")
    assert active.status_label() == "Effectif 2026-05"
    assert active.sortie_label() == "—"

    hist = PersonnelSearchHit(
        kind="historical",
        row=row,
        sortie=SortieSummary("PERTE", "", "DECES", "2026-04"),
        last_carriere_mois="2026-04",
        active_mois_label="2026-05",
    )
    assert hist.status_label() == "Hors effectif"
    assert "DECES" in hist.sortie_label()


def test_extended_search_uses_batched_repository_results() -> None:
    row = ListeGeneraleRow(
        id="1",
        noms="SEFFO",
        date_naissance=None,
        lieu_naissance=None,
        grade="COL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire="25496",
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    service = PersonnelLookupService.__new__(PersonnelLookupService)
    service._settings = object()
    service._agents = MagicMock()
    service._agents.search_current_personnel.return_value = [row]
    hist_row = ListeGeneraleRow(
        id="2",
        noms="SEFFO",
        date_naissance=None,
        lieu_naissance=None,
        grade="COL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire="99999",
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    service._agents.search_historical_personnel.return_value = [
        (2, hist_row, "2026-04", ("PERTE", "", "DECES", "2026-04"))
    ]
    service.active_mois = MagicMock(return_value="2026-05")
    service.fetch_sortie_summary = MagicMock()

    hits = service.search_extended("SE")

    assert [hit.kind for hit in hits] == ["active", "historical"]
    assert hits[1].sortie == SortieSummary("PERTE", "", "DECES", "2026-04")

    service._agents.search_historical_personnel.return_value = []
    service._agents.search_current_personnel.return_value = [row, row]
    deduped = service.search_extended("SE")
    assert len(deduped) == 1
    assert deduped[0].kind == "active"

    service._agents.search_current_personnel.return_value = [row]
    active_only = service.search_active_effectif("SE")
    assert len(active_only) == 1
    assert active_only[0].kind == "active"
    service._agents.search_current_personnel.assert_called_with("SE")
    service._agents.search_historical_personnel.assert_called_with("SE")
    service.fetch_sortie_summary.assert_not_called()
