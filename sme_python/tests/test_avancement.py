"""Moteur d'avancement et barème unique de l'article 98."""

from __future__ import annotations

from datetime import date

from sme_gend.models.avancement import (
    STATUT_A_PREPARER,
    STATUT_ELIGIBLE,
    STATUT_FENETRE,
    STATUT_HORS,
    STATUT_INCOMPLET,
    STATUT_SUIVI,
    AgentAvancementInput,
)
from sme_gend.services.avancement_engine import cibles_possibles, evaluer, evaluer_agent
from sme_gend.services.limite_age_service import _find_default_limits, statutory_default_limits
from sme_gend.services.statutory_limits import article98_limits, canonical_grade


def _agent(**kwargs) -> AgentAvancementInput:
    base = dict(
        agent_id=1,
        noms="NGUESSO Paul",
        grade_raw="MDL",
        date_grade=date(2018, 1, 1),
        date_service=date(2016, 1, 1),
        date_naissance=date(1990, 1, 1),
        diplomes=("OPJ",),
        maniere_servir="favorable",
        campagne_statut="inscrit",
    )
    base.update(kwargs)
    return AgentAvancementInput(**base)


def test_mdc_est_le_code_du_marechal_des_logis_chef() -> None:
    assert canonical_grade("MDL chef") == "MDC"
    assert canonical_grade("Maréchal des logis-chef") == "MDC"
    assert canonical_grade("Maréchal des Logis") == "MDL"


def test_article_98_est_la_source_des_attestations() -> None:
    assert article98_limits("MDC") == (55, 37)
    assert _find_default_limits("Maréchal des Logis") == (55, 37)
    assert _find_default_limits("MDL") == (55, 37)
    assert statutory_default_limits("Adjudant-Chef") == (58, 40)
    assert statutory_default_limits("Sous-Lieutenant") == (60, 42)
    assert statutory_default_limits("Colonel-Major") == (65, 47)
    assert statutory_default_limits("Général de Brigade") == (70, 52)
    assert _find_default_limits("Sergent-Chef") == (55, 37)
    assert _find_default_limits("Grade sans barème") is None


def test_adjudant_chef_a_deux_voies() -> None:
    assert cibles_possibles("ADC") == ["ADM", "SLT"]
    assert cibles_possibles("ADM") == []


def test_mdl_eligible_mdc_quand_avis_et_tableau() -> None:
    row = evaluer(_agent(), on=date(2022, 1, 1))
    assert row.grade_cible == "MDC"
    assert row.statut == STATUT_ELIGIBLE


def test_diplome_manquant_reste_a_preparer() -> None:
    row = evaluer(_agent(diplomes=()), on=date(2022, 1, 1))
    assert row.statut == STATUT_A_PREPARER
    assert any(cond.code == "diplome" and cond.ok is False for cond in row.conditions)


def test_dqsg2_vaut_pour_dqsg1() -> None:
    row = evaluer(
        _agent(
            grade_raw="MDC",
            date_grade=date(2014, 1, 1),
            date_service=date(2010, 1, 1),
            diplomes=("DQSG 2",),
        ),
        on=date(2020, 1, 1),
    )
    assert row.grade_cible == "ADJ"
    assert row.statut == STATUT_ELIGIBLE


def test_fenetre_courte_si_la_limite_precede_le_temps_de_grade() -> None:
    row = evaluer(
        _agent(
            date_naissance=date(1971, 6, 15),
            date_grade=date(2024, 6, 15),
            date_service=date(2000, 1, 1),
            maniere_servir=None,
            campagne_statut=None,
        ),
        on=date(2026, 1, 1),
    )
    assert row.statut == STATUT_FENETRE


def test_lieutenant_issu_ecole_en_deux_ans() -> None:
    row = evaluer(
        _agent(
            grade_raw="SLT",
            date_grade=date(2024, 1, 1),
            date_service=date(2010, 1, 1),
            diplomes=(),
            issu_ecole=True,
            date_naissance=date(1990, 1, 1),
        ),
        on=date(2026, 1, 1),
    )
    assert row.grade_cible == "LTN"
    assert row.statut == STATUT_ELIGIBLE


def test_sans_date_de_passation_le_dossier_est_incomplet() -> None:
    row = evaluer(_agent(date_grade=None), on=date(2022, 1, 1))
    assert row.statut == STATUT_INCOMPLET


def test_passage_mdl_hors_calcul() -> None:
    rows = evaluer_agent(
        _agent(grade_raw="Gendarme", diplomes=(), maniere_servir=None, campagne_statut=None),
        on=date(2026, 1, 1),
    )
    assert len(rows) == 1
    assert rows[0].grade_cible == "MDL"
    assert rows[0].statut == STATUT_HORS


def test_horizon_loin_reste_en_suivi() -> None:
    row = evaluer(
        _agent(date_grade=date(2024, 1, 1), maniere_servir=None, campagne_statut=None),
        on=date(2024, 6, 1),
        horizon_days=365,
    )
    assert row.statut == STATUT_SUIVI
