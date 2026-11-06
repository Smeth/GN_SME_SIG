"""Tests Phase E — cache liste et alertes carrière."""

from __future__ import annotations

from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.career_import_alerts import _scan_agent_snaps
from sme_gend.services import liste_generale_cache as cache_mod


class _Settings:
    mysql_view_mois = "2026-05"


def test_cached_liste_rows_reuses_cache() -> None:
    settings = _Settings()  # type: ignore[arg-type]
    cache_mod.invalidate_liste_cache(settings)  # type: ignore[arg-type]
    sample = [
        ListeGeneraleRow(
            id="1",
            noms="Dupont",
            date_naissance=None,
            lieu_naissance=None,
            grade="MDL",
            date_grade=None,
            date_service=None,
            ndecision=None,
            matricule_militaire=None,
            matricule_solde=None,
            position_admin=None,
            fonction=None,
            subdivision_arme=None,
            diplome=None,
            date_obtention=None,
            structure="A",
            obs=None,
            ordre_tri=None,
            presseance=None,
        )
    ]
    key = cache_mod.liste_cache_key(settings)  # type: ignore[arg-type]
    cache_mod._rows_cache[key] = sample
    assert cache_mod.cached_liste_rows(settings) is sample  # type: ignore[arg-type]
    cache_mod.invalidate_liste_cache(settings)  # type: ignore[arg-type]


def test_scan_agent_structure_cleared() -> None:
    snaps = [
        CarriereSnapshotRow(
            annee_mois="2026-01",
            date_debut=None,
            date_fin=None,
            grade=None,
            date_grade=None,
            structure="A",
            position_admin=None,
            fonction=None,
            subdivision_arme=None,
            date_service=None,
            ndecision=None,
            diplome=None,
        ),
        CarriereSnapshotRow(
            annee_mois="2026-02",
            date_debut=None,
            date_fin=None,
            grade=None,
            date_grade=None,
            structure="",
            position_admin=None,
            fonction=None,
            subdivision_arme=None,
            date_service=None,
            ndecision=None,
            diplome=None,
        ),
    ]
    alerts = _scan_agent_snaps("42", "Test", snaps)
    assert any(a.code == "STRUCTURE_CLEARED" for a in alerts)
