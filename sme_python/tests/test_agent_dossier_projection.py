"""Tests projection dossier agent → carriere_situation (Phase C)."""

from __future__ import annotations
from contextlib import nullcontext

from datetime import date

import pytest

from sme_gend.models.agent_dossier import AgentAffectationRow, AgentDiplomeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.agent_dossier_service import AgentDossierService
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
        self.sync_calls: list[tuple[int, str]] = []
        self.diplomes: list[dict] = []
        self.passations: list[dict] = []
        self.affectations: list[dict] = []

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

    def insert_diplome_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.diplomes.append({"agent_id": agent_id, **kwargs})
        return len(self.diplomes)

    def insert_passation_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.passations.append({"agent_id": agent_id, **kwargs})
        return len(self.passations)

    def insert_affectation_if_absent(self, agent_id: int, **kwargs) -> int | None:
        self.affectations.append({"agent_id": agent_id, **kwargs})
        return len(self.affectations)

    def sync_carriere_from_dossier(self, agent_id: int, carriere_id: str, **kwargs) -> None:
        self.sync_calls.append((agent_id, carriere_id))

    def list_affectations(self, agent_id: int) -> list[AgentAffectationRow]:
        return [AgentAffectationRow(id=12, agent_id=agent_id, structure_code="U001", date_affectation=date(2026, 9, 1))]

    def insert_affectation(self, agent_id: int, **kwargs) -> int:
        self.affectations.append({"agent_id": agent_id, **kwargs})
        return len(self.affectations)

    def delete_affectation(self, affectation_id: int) -> None:
        pass

    def delete_passation(self, passation_id: int) -> None:
        pass

    def insert_diplome(self, agent_id: int, **kwargs) -> int:
        self.diplomes.append({"agent_id": agent_id, **kwargs})
        return len(self.diplomes)

    def list_diplomes(self, agent_id: int) -> list[AgentDiplomeRow]:
        return [
            AgentDiplomeRow(
                id=1,
                agent_id=agent_id,
                categorie="CIVIL",
                libelle="DEMFS",
                obs="Import auto",
            )
        ]

    def update_diplome(self, diplome_id: int, **kwargs) -> None:
        self.diplomes.append({"id": diplome_id, **kwargs})

    def delete_diplome(self, diplome_id: int) -> None:
        pass


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


def test_sync_dossier_after_save_projects_carriere(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    cache_cleared: list[object] = []

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_sync.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr("sme_gend.services.agent_dossier_sync._active_mois", lambda: "2020-03")
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda settings: cache_cleared.append(settings),
    )

    before = _row(grade="MDL")
    after = _row(grade="ADC", date_grade=date(2020, 3, 15))
    sync_dossier_after_personnel_save(object(), before, after)

    assert len(repo.passations) == 1
    assert repo.sync_calls == [(7, "100")]
    assert len(cache_cleared) == 1


def test_project_carriere_after_affectation(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    repo.sync_calls = []

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _s: None,
    )

    svc = AgentDossierService(object())
    row = _row()
    svc.add_affectation(
        row,
        structure_code="U002",
        date_affectation=date(2020, 3, 1),
        fonction="Adjoint",
        subdivision_arme="Cav",
    )

    assert repo.sync_calls == [(7, "100")]


def test_update_diplome_projects_carriere(monkeypatch) -> None:
    repo = _FakeDossierRepo()

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.assert_may_mutate_dossier_history",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "sme_gend.services.sensitive_data_policy.assert_may_mutate_imported_dossier_entry",
        lambda **_: None,
    )

    svc = AgentDossierService(object())
    row = _row()
    svc.update_diplome(row, 1, expected=repo.list_diplomes(7)[0], categorie="CIVIL", libelle="DEMFS modifié")

    assert repo.sync_calls == [(7, "100")]
    assert hasattr(svc, "update_diplome")


def test_delete_affectation_projects_carriere(monkeypatch) -> None:
    repo = _FakeDossierRepo()

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _s: None,
    )

    svc = AgentDossierService(object())
    svc.delete_affectation(_row(), 12, expected=repo.list_affectations(7)[0])

    assert repo.sync_calls == [(7, "100")]


@pytest.fixture(autouse=True)
def repository_isolated_transaction(monkeypatch):
    monkeypatch.setattr("sme_gend.repositories.write_guard.lock_agent", lambda *a, **kw: 7)
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write",
                        lambda *args, **kwargs: nullcontext())


# Persistence is covered on native MySQL; this file exercises its fake repository.
import pytest

@pytest.fixture(autouse=True)
def isolated_audit_persistence(monkeypatch):
    monkeypatch.setattr('sme_gend.services.sensitive_audit_log._append_entry',lambda *a,**kw:None)
