"""Tests resync carrière après mutations passations dossier."""

from __future__ import annotations
from contextlib import nullcontext

from datetime import date

import pytest

from sme_gend.models.agent_dossier import AgentPassationGradeRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.agent_dossier_service import AgentDossierService


class _FakeDossierRepo:
    def apply_dated_grade(self, agent_id, before, effective_date, **kwargs):
        self.sync_grade_calls.append((agent_id, str(before.id)))

    def __init__(self, agent_id: int = 7) -> None:
        self.agent_id = agent_id
        self.deleted_passation_ids: list[int] = []
        self.sync_grade_calls: list[tuple[int, str]] = []

    def resolve_agent_id(self, *, carriere_id: str | None = None, legacy_id: str | None = None) -> int:
        return self.agent_id

    def list_passations(self, agent_id: int) -> list[AgentPassationGradeRow]:
        return [
            AgentPassationGradeRow(
                id=55,
                agent_id=agent_id,
                grade_libelle="MDL",
                date_passation=date(2019, 6, 1),
            )
        ]

    def delete_passation(self, passation_id: int) -> None:
        self.deleted_passation_ids.append(passation_id)

    def sync_carriere_from_dossier(self, agent_id: int, carriere_id: str, **kwargs) -> None:
        self.sync_grade_calls.append((agent_id, carriere_id))

    def insert_passation(self, agent_id: int, **kwargs) -> int:
        return 99

    def update_passation(self, passation_id: int, **kwargs) -> None:
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
    base.update(kwargs)
    return ListeGeneraleRow(**base)


def test_delete_passation_resyncs_carriere_and_invalidates_cache(monkeypatch) -> None:
    repo = _FakeDossierRepo(agent_id=42)
    cache_invalidated: list[object] = []

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda settings: cache_invalidated.append(settings),
    )

    svc = AgentDossierService(object())
    svc.delete_passation(_row(), 55, expected=repo.list_passations(42)[0])

    assert repo.deleted_passation_ids == [55]
    assert repo.sync_grade_calls == [(42, "100")]
    assert len(cache_invalidated) == 1


def test_add_passation_invalidates_cache_when_sync_fiche(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    cache_invalidated: list[object] = []

    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda settings: cache_invalidated.append(settings),
    )

    svc = AgentDossierService(object())
    svc.add_passation(_row(), grade_libelle="ADC", sync_fiche=True)

    assert repo.sync_grade_calls == [(7, "100")]
    assert len(cache_invalidated) == 1


def test_add_passation_skips_projection_when_no_sync_fiche(monkeypatch) -> None:
    repo = _FakeDossierRepo()
    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_service.MySQLAgentDossierRepository",
        lambda _s: repo,
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda settings: None,
    )

    svc = AgentDossierService(object())
    svc.add_passation(_row(), grade_libelle="ADC", sync_fiche=False)
    assert repo.sync_grade_calls == []


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
