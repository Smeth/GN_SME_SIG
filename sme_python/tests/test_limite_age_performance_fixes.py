from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from sme_gend.models.limite_age_config import LimiteAgeConfig
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.limite_age_service import (
    LimiteAgeService,
    _find_default_limits,
)
from sme_gend.ui.views.limite_age_view import (
    _add_calendar_years,
    _is_within_next_two_years,
)


def _person(grade: str = "Grade inconnu") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=1,
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade=grade,
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
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_unknown_grade_does_not_receive_guessed_default() -> None:
    assert _find_default_limits("Grade sans barème") is None


def test_sync_adds_unknown_grade_disabled_until_configured(monkeypatch) -> None:
    from sme_gend.models.grade_ref import GradeRefRow
    from sme_gend.repositories import grade_repository

    class Repository:
        def __init__(self, _settings) -> None:
            pass

        def fetch_all(self):
            return [GradeRefRow("UNK", "Grade sans barème", 1, 0)]

    monkeypatch.setattr(grade_repository, "GradeRepository", Repository)
    service = LimiteAgeService()
    monkeypatch.setattr(service, "load", lambda _settings: LimiteAgeConfig())
    saved = []
    monkeypatch.setattr(service, "save_grades", lambda cfg, _settings: saved.append(cfg))

    config, added = service.sync_grades_from_referentiel(object())

    assert added == ["Grade sans barème"]
    assert (config.grades[0].limite_age, config.grades[0].limite_service) == (0, 0)
    assert saved == [config]


def test_unknown_grade_cannot_generate_attestation_without_configured_limit(
    tmp_path,
) -> None:
    service = LimiteAgeService()
    with pytest.raises(ValueError, match="Configurez la limite"):
        service.generate_docx(_person(), LimiteAgeConfig(), tmp_path / "out.docx")
    with pytest.raises(ValueError, match="Configurez la limite"):
        service.generate_html(_person(), LimiteAgeConfig())


def test_two_year_filter_excludes_past_and_dates_after_calendar_horizon() -> None:
    today = date(2024, 2, 29)
    assert _add_calendar_years(today, 2) == date(2026, 2, 28)
    assert not _is_within_next_two_years(date(2024, 2, 28), today)
    assert _is_within_next_two_years(today, today)
    assert _is_within_next_two_years(date(2026, 2, 28), today)
    assert not _is_within_next_two_years(date(2026, 3, 1), today)
    assert not _is_within_next_two_years(None, today)


def test_grade_referential_errors_are_not_silently_swallowed(monkeypatch) -> None:
    from sme_gend.repositories import grade_repository

    class BrokenRepository:
        def __init__(self, _settings) -> None:
            pass

        def fetch_all(self):
            raise RuntimeError("referential unavailable")

    monkeypatch.setattr(grade_repository, "GradeRepository", BrokenRepository)
    service = LimiteAgeService()
    monkeypatch.setattr(service, "load", lambda _settings: LimiteAgeConfig())

    with pytest.raises(RuntimeError, match="referential unavailable"):
        service.sync_grades_from_referentiel(object())


def test_checked_personnel_selection_is_refreshed_and_removed_when_missing() -> None:
    from sme_gend.services.limite_age_service import LimiteDateResult
    from sme_gend.ui.views.limite_age_view import _PersonnelModel, _person_key

    model = _PersonnelModel()
    old_person = _person("Grade ancien")
    fresh_person = replace(old_person, grade="Grade actualisé")
    another_person = _person("Autre")
    old_result = LimiteDateResult(None, "—", None, None, None)
    fresh_result = LimiteDateResult(None, "—", None, None, None)
    model._checked[_person_key(old_person)] = (old_person, old_result)
    model._checked[_person_key(another_person)] = (another_person, old_result)

    model.refresh_checked([(fresh_person, fresh_result)])

    assert model.checked_rows() == [(fresh_person, fresh_result)]
