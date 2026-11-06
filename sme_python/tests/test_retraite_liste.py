"""Liste des retraites selon la limite et la durée."""

from datetime import date
from types import SimpleNamespace

from sme_gend.services.retraite_liste import (
    ECHEANCE_ATTEINTE,
    ECHEANCE_DANS,
    ECHEANCE_TOUTES,
    MOTIF_AGE,
    MOTIF_SERVICE,
    filtrer_retraites,
    format_duree_restante,
)


def _person(nom: str) -> SimpleNamespace:
    return SimpleNamespace(noms=nom, date_naissance=None, date_service=None)


def _limit(age: date | None, service: date | None) -> SimpleNamespace:
    return SimpleNamespace(age_limit_date=age, service_limit_date=service)


def test_filtre_limite_et_duree() -> None:
    today = date(2026, 9, 23)
    rows = [
        (_person("AGE PROCHE"), _limit(date(2027, 12, 31), date(2035, 12, 31))),
        (_person("SERVICE PROCHE"), _limit(date(2040, 12, 31), date(2026, 6, 1))),
        (_person("LOIN"), _limit(date(2034, 12, 31), None)),
    ]
    ages = filtrer_retraites(
        rows,
        motif=MOTIF_AGE,
        echeance=ECHEANCE_DANS,
        annees=2,
        today=today,
    )
    assert [row.person.noms for row in ages] == ["AGE PROCHE"]
    assert ages[0].motif == "Âge"
    plus_court = filtrer_retraites(
        rows,
        motif=MOTIF_AGE,
        echeance=ECHEANCE_DANS,
        annees=1,
        today=today,
    )
    assert plus_court == []

    services = filtrer_retraites(
        rows,
        motif=MOTIF_SERVICE,
        echeance=ECHEANCE_ATTEINTE,
        today=today,
    )
    assert [row.person.noms for row in services] == ["SERVICE PROCHE"]
    assert services[0].jours_restants < 0

    toutes = filtrer_retraites(
        rows,
        motif=MOTIF_AGE,
        echeance=ECHEANCE_TOUTES,
        today=today,
    )
    assert [row.person.noms for row in toutes] == ["AGE PROCHE", "LOIN", "SERVICE PROCHE"]
    assert "Atteinte" in format_duree_restante(-10)
    assert format_duree_restante(400).startswith("1 an")
