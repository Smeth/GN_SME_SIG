"""Tests synchronisation dossier après saisie manuelle (SoT P0.1)."""

from __future__ import annotations

from datetime import date, datetime
import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.agent_dossier_sync import sync_dossier_after_personnel_save


class _FakeDossierRepo:
    def assert_carriere_affectation(self, *args, **kwargs):
        pass

    def apply_dated_grade(self, agent_id, before, effective_date, *, carriere_id, **kwargs):
        self.sync_calls.append((agent_id, str(carriere_id)))

    def assert_carriere_grade(self, *args, **kwargs):
        pass

    def __init__(self, agent_id: int = 7) -> None:
        self.agent_id = agent_id
        self.diplomes: list[dict] = []
        self.passations: list[dict] = []
        self.affectations: list[dict] = []
        self.sync_calls: list[tuple[int, str]] = []

    def resolve_agent_id(self, *, carriere_id: str | None = None, legacy_id: str | None = None) -> int:
        return self.agent_id

    def record_list_saisie_diplome(self, agent_id: int, **kwargs) -> int | None:
        self.diplomes.append({"agent_id": agent_id, **kwargs})
        return len(self.diplomes)

    def record_list_saisie_passation(self, agent_id: int, **kwargs) -> int | None:
        self.passations.append({"agent_id": agent_id, **kwargs})
        return len(self.passations)

    def record_list_saisie_affectation(self, agent_id: int, **kwargs) -> int | None:
        self.affectations.append({"agent_id": agent_id, **kwargs})
        return len(self.affectations)

    def sync_carriere_from_dossier(self, agent_id: int, carriere_id: str, **kwargs) -> None:
        self.sync_calls.append((agent_id, carriere_id))


def _patch_sync(monkeypatch, repo: _FakeDossierRepo) -> None:
    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_sync.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr("sme_gend.services.agent_dossier_sync._active_mois", lambda: "2020-03")
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _s: None,
    )


def _row(**kwargs) -> ListeGeneraleRow:
    base = dict(
        id="100",
        noms="DUPONT",
        date_naissance=None,
        lieu_naissance=None,
        grade="MDL",
        date_grade=date(2019, 6, 1),
        date_service=None,
        ndecision="10/GR",
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction="Chef",
        subdivision_arme="Inf",
        diplome="BIA",
        date_obtention="2018-01-15",
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    base.update(kwargs)
    return ListeGeneraleRow(**base)


def test_sync_diplome_on_edit(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)

    before = _row(diplome="BIA", date_obtention="2018-01-15")
    after = _row(diplome="Master", date_obtention="2020-05-10")
    sync_dossier_after_personnel_save(object(), before, after)

    assert len(repo.diplomes) == 1
    assert repo.diplomes[0]["libelle"] == "Master"
    assert repo.diplomes[0]["date_obtention"] == date(2020, 5, 10)
    assert repo.diplomes[0]["obs"] == "Saisie applicative"
    assert repo.passations == []
    assert repo.affectations == []
    assert repo.sync_calls == [(7, "100")]


def test_sync_passation_on_grade_change(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)

    before = _row(grade="MDL", date_grade=date(2019, 6, 1))
    after = _row(grade="ADC", date_grade=date(2020, 3, 15), ndecision="22/GR")
    sync_dossier_after_personnel_save(object(), before, after)

    assert len(repo.passations) == 1
    assert repo.passations[0]["grade_libelle"] == "ADC"
    assert repo.passations[0]["date_passation"] == date(2020, 3, 15)
    assert repo.passations[0]["ndecision"] == "22/GR"
    assert repo.sync_calls == [(7, "100")]


def test_sync_affectation_on_structure_change(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)

    before = _row(structure="U001", fonction="Chef", subdivision_arme="Inf")
    after = _row(structure="U002", fonction="Adjoint", subdivision_arme="Cav")
    sync_dossier_after_personnel_save(object(), before, after)

    assert len(repo.affectations) == 1
    assert repo.affectations[0]["structure_code"] == "U002"
    assert repo.affectations[0]["date_affectation"] == date(2020, 3, 1)
    assert repo.affectations[0]["fonction"] == "Adjoint"
    assert repo.affectations[0]["subdivision_arme"] == "Cav"
    assert repo.affectations[0]["sme_periode_mois"] == "2020-03"


def test_sync_create_with_initial_values(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)

    empty = _row(
        grade=None,
        date_grade=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        fonction=None,
        subdivision_arme=None,
    )
    after = _row(
        grade="MDL",
        date_grade=datetime(2019, 6, 1),
        diplome="BIA",
        date_obtention="2018-01-15",
        structure="U001",
    )
    sync_dossier_after_personnel_save(object(), empty, after)

    assert len(repo.diplomes) == 1
    assert len(repo.passations) == 1
    assert len(repo.affectations) == 1
    assert repo.sync_calls == [(7, "100"), (7, "100"), (7, "100")]


def test_sync_affectation_on_fonction_change(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)

    before = _row(structure="U001", fonction="Chef", subdivision_arme="Inf")
    after = _row(structure="U001", fonction="Adjoint", subdivision_arme="Inf")
    sync_dossier_after_personnel_save(object(), before, after)

    assert len(repo.affectations) == 1
    assert repo.affectations[0]["fonction"] == "Adjoint"
    assert repo.affectations[0]["structure_code"] == "U001"


def test_sync_no_op_when_unchanged(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)
    row = _row()
    sync_dossier_after_personnel_save(object(), row, row)
    assert repo.diplomes == []
    assert repo.passations == []
    assert repo.affectations == []
    assert repo.sync_calls == []


def test_sync_profile_only_does_not_project(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)
    before = _row(noms="DUPONT A")
    after = _row(noms="DUPONT Albert")
    sync_dossier_after_personnel_save(object(), before, after)
    assert repo.sync_calls == []


def test_monthly_grade_cannot_be_cleared_without_history_edit():
    from sme_gend.services.agent_dossier_sync import validate_monthly_history_edit
    with pytest.raises(ValueError, match='Passations'):
        validate_monthly_history_edit(_row(grade='MDL'), _row(grade=None))


def test_unresolved_agent_does_not_silently_skip_history(monkeypatch):
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)
    def fail(**kwargs):
        raise ValueError('Agent introuvable')
    repo.resolve_agent_id = fail
    with pytest.raises(ValueError, match='Agent introuvable'):
        sync_dossier_after_personnel_save(object(), _row(), _row(grade='ADC'))
