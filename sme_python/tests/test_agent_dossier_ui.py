"""Tests saisie dates et modèles dossier agent."""

from __future__ import annotations

from datetime import datetime

from sme_gend.models.agent_dossier import AgentDiplomeRow, AgentPassationGradeRow, DIPLOME_MILITAIRE


def test_agent_diplome_from_sequence() -> None:
    row = AgentDiplomeRow.from_sequence(
        (1, 2, "MILITAIRE", "BIA", "2020-06-15", "Libreville", None, None, None, None)
    )
    assert row.categorie == DIPLOME_MILITAIRE
    assert row.libelle == "BIA"


def test_agent_passation_from_sequence() -> None:
    row = AgentPassationGradeRow.from_sequence(
        (1, 2, "LT", "2019-03-01", "123/GR", "—", 5)
    )
    assert row.grade_libelle == "LT"
    assert row.grade_id == 5


def test_french_date_format_digits() -> None:
    from sme_gend.ui.widgets.date_input import _digits_only, _format_digits, _parse_date_str

    assert _digits_only("15/06/2020") == "15062020"
    assert _format_digits("15062020") == "15/06/2020"
    assert _format_digits("15") == "15"
    assert _format_digits("1506") == "15/06"

    qd = _parse_date_str("15/06/2020")
    assert qd is not None
    assert qd.year() == 2020
    assert qd.month() == 6
    assert qd.day() == 15
    assert _parse_date_str("2020-06-15") is not None
    assert _parse_date_str("") is None
