"""Tests enrichissement dossier à l'import."""

from __future__ import annotations

from datetime import date

from sme_gend.models.agent_evenement import CarriereSnapshotRow
from sme_gend.services.agent_dossier_import_enrichment import (
    _diplome_date,
    _grade_date,
    _maybe_add_affectation,
    _maybe_add_diplome,
    _maybe_add_passation,
    _structure_date,
    enrich_agent_from_snapshots,
)
from sme_gend.services.mysql_import_period import first_day_of_month


class _FakeDossierRepo:
    def __init__(self) -> None:
        self.diplomes: list[dict] = []
        self.passations: list[dict] = []
        self.affectations: list[dict] = []

    def insert_diplome_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.diplomes.append({"agent_id": agent_id, **kwargs})
        return len(self.diplomes)

    def insert_passation_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.passations.append({"agent_id": agent_id, **kwargs})
        return len(self.passations)

    def insert_affectation_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.affectations.append({"agent_id": agent_id, **kwargs})
        return len(self.affectations)


def _snap(**kwargs) -> CarriereSnapshotRow:
    base = dict(
        annee_mois="2020-03",
        date_debut="2020-03-01",
        date_fin=None,
        grade="MDL",
        date_grade="2019-06-01",
        structure="U001",
        position_admin=None,
        fonction="Chef",
        subdivision_arme="Inf",
        date_service=None,
        ndecision="10/GR",
        diplome="BIA",
        date_obtention="2018-01-15",
        est_actuelle=False,
    )
    base.update(kwargs)
    return CarriereSnapshotRow(**base)


def test_grade_date_fallback_mois() -> None:
    s = _snap(date_grade=None)
    assert _grade_date(s, "2020-03") == first_day_of_month("2020-03")


def test_structure_date_first_day() -> None:
    assert _structure_date("2021-07") == date(2021, 7, 1)


def test_diplome_date_parse() -> None:
    assert _diplome_date(_snap()) == date(2018, 1, 15)


def test_maybe_add_helpers() -> None:
    repo = _FakeDossierRepo()
    snap = _snap()
    assert _maybe_add_diplome(repo, 1, snap, "Import archive 2020-03", 5) == 1
    assert _maybe_add_passation(repo, 1, snap, "2020-03", "Import archive 2020-03") == 1
    assert _maybe_add_affectation(repo, 1, snap, "2020-03", "Import archive 2020-03", 5) == 1
    assert repo.diplomes[0]["libelle"] == "BIA"
    assert repo.passations[0]["grade_libelle"] == "MDL"
    assert repo.affectations[0]["structure_code"] == "U001"


def test_enrich_agent_no_legacy(monkeypatch) -> None:
    class _Repo:
        def agent_id_for_legacy(self, _leg: str) -> None:
            return None

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_import_enrichment.MySQLAgentDossierRepository",
        lambda _s: _Repo(),
    )
    c = enrich_agent_from_snapshots(object(), "42", "2020-03")
    assert c.total() == 0
