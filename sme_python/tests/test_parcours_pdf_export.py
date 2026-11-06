"""Tests export PDF dossier agent."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.parcours_pdf_export import build_dossier_html


@pytest.fixture(autouse=True)
def position_events_without_database(monkeypatch):
    monkeypatch.setattr(
        "sme_gend.services.position_admin_events.list_position_events",
        lambda settings, legacy_id: [],
    )


def _row(**kw) -> ListeGeneraleRow:
    base = ListeGeneraleRow(
        id="42",
        noms="DUPONT Jean",
        date_naissance=datetime(1985, 3, 12),
        lieu_naissance="Libreville",
        grade="LTN",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire="12345",
        matricule_solde=None,
        position_admin="Présent",
        fonction="Chef section",
        subdivision_arme="DPF",
        diplome=None,
        date_obtention=None,
        structure="DPF",
        obs=None,
        ordre_tri=None,
        presseance=None,
        sexe="M",
    )
    return replace(base, **kw)


def test_build_dossier_html_structure(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.position_admin_events.list_position_events",
        lambda settings, legacy_id: [
            ("Détaché", "2026-10-29", None, "Organisme accueil", "DEC-42", "Renfort", "2026-10")
        ],
    )
    class _EmptyDossier:
        def __init__(self, *_a, **_k) -> None:
            pass

        def list_passations(self, _row):
            return []

        def list_affectations(self, _row):
            return []

        def list_diplomes(self, _row):
            return []

    class _EmptyEvents:
        def __init__(self, *_a, **_k) -> None:
            pass

        def missions(self, _leg):
            return []

        def stages(self, _leg):
            return []

        def malades(self, _leg):
            return []

        def desertions(self, _leg):
            return []

        def conges(self, _leg):
            return []

    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.AgentDossierService",
        _EmptyDossier,
    )
    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.AgentDossierEventsService",
        _EmptyEvents,
    )

    class _GradesSvc:
        def __init__(self, *_a, **_k) -> None:
            pass

        def grades(self):
            from sme_gend.models.grade_ref import GradeRefRow

            return [GradeRefRow(code="LTN", libelle="Lieutenant", pref_ord=1, effectif_g=None)]

    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.ReferentielService",
        _GradesSvc,
    )

    class _Settings:
        mysql_view_mois = "2026-07"

    html = build_dossier_html(_row(), _Settings())
    assert "DEC-42" in html
    assert "Organisme accueil" in html
    assert "DUPONT Jean" in html or "DUPONT" in html
    assert "FICHE INDIVIDUELLE" in html
    assert "FICHE INDIVIDUELLE MILITAIRE" not in html
    assert html.count("FICHE INDIVIDUELLE") == 1
    assert "IDENTIT" in html.upper()
    assert "HISTORIQUE DES GRADES" not in html
    assert "Page 1 / 3" not in html
    assert "Page 2 / 3" not in html
    assert "Page 3 / 3" not in html
    assert "POSITIONS ADMINISTRATIVES ET ABSENCES" in html.upper()
    assert "GABON" not in html
    assert "MINISTÈRE" not in html
    assert "Nationalité" not in html


def test_fonction_vide_laisse_le_tableau_affectations_vide(monkeypatch) -> None:
    from datetime import date

    from sme_gend.models.agent_dossier import AgentAffectationRow

    class _Dossier:
        def __init__(self, *_a, **_k) -> None:
            pass

        def list_passations(self, _row):
            return []

        def list_affectations(self, _row):
            return [
                AgentAffectationRow(
                    id=1,
                    agent_id=1,
                    structure_code="DPF",
                    date_affectation=date(2026, 1, 1),
                    fonction="",
                    sme_periode_mois="2026-01",
                )
            ]

        def list_diplomes(self, _row):
            return []

    class _EmptyEvents:
        def __init__(self, *_a, **_k) -> None:
            pass

        def missions(self, _leg):
            return []

        def stages(self, _leg):
            return []

        def malades(self, _leg):
            return []

        def desertions(self, _leg):
            return []

        def conges(self, _leg):
            return []

    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.AgentDossierService",
        _Dossier,
    )
    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.AgentDossierEventsService",
        _EmptyEvents,
    )
    monkeypatch.setattr(
        "sme_gend.services.parcours_pdf_export.ReferentielService",
        type("R", (), {"__init__": lambda self, *_a, **_k: None, "grades": lambda self: []}),
    )

    class _Settings:
        mysql_view_mois = "2026-07"

    html = build_dossier_html(_row(fonction=""), _Settings())
    assert "Aucun enregistrement" not in html
    assert "HISTORIQUE DES AFFECTATIONS" not in html.upper()
    assert "2026-01" not in html
