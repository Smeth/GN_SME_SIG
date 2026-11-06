from __future__ import annotations

from datetime import datetime

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_deleted_service import (
    DeletedPersonnelStatus,
    assess_deleted_restore,
)


class _Impl:
    def __init__(self, agent_by_career: dict[str, int], legacy_agent: dict[str, int]) -> None:
        self._agent_by_career = agent_by_career
        self._legacy_agent = legacy_agent

    def fetch_agent_id_for_career(self, career_id: str) -> int | None:
        return self._agent_by_career.get(career_id)

    def fetch_agent_id_by_legacy(self, legacy: str) -> int | None:
        return self._legacy_agent.get(legacy)

    def career_segment_exists(self, career_id: str) -> bool:
        return career_id in self._agent_by_career


class _Repo:
    def __init__(self, rows: list[ListeGeneraleRow], impl: _Impl) -> None:
        self._rows = rows
        self._impl = impl

    def fetch_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        for r in self._rows:
            if r.id == row_id:
                return r
        return None

    def fetch_all(self) -> list[ListeGeneraleRow]:
        return list(self._rows)


def _row(**kwargs) -> ListeGeneraleRow:
    base = {
        "id": "1",
        "noms": "TEST",
        "date_naissance": None,
        "lieu_naissance": None,
        "grade": "ADJ",
        "date_grade": None,
        "date_service": None,
        "ndecision": None,
        "matricule_militaire": "MM1",
        "matricule_solde": None,
        "position_admin": None,
        "fonction": None,
        "subdivision_arme": None,
        "diplome": None,
        "date_obtention": None,
        "structure": None,
        "obs": None,
        "ordre_tri": None,
        "presseance": None,
        "access_join_id": "LEG-99",
    }
    base.update(kwargs)
    return ListeGeneraleRow(**base)


def test_assess_already_present_same_legacy_after_restore() -> None:
    deleted = _row(id="42", access_join_id="LEG-99", matricule_militaire="MM1")
    active = _row(id="99", access_join_id="LEG-99", matricule_militaire="MM1", noms="TEST actif")
    impl = _Impl(agent_by_career={"99": 4591}, legacy_agent={"LEG-99": 4591})
    repo = _Repo([active], impl)
    st, _detail = assess_deleted_restore(repo, deleted, "42")
    assert st == DeletedPersonnelStatus.ALREADY_PRESENT


def test_assess_restorable_when_matricule_cleared() -> None:
    deleted = _row(id="42", access_join_id="LEG-A", matricule_militaire=None, matricule_solde=None)
    other = _row(id="77", access_join_id="LEG-B", matricule_militaire="MM1", noms="AUTRE")
    impl = _Impl(agent_by_career={"42": 1, "77": 2}, legacy_agent={"LEG-A": 1})
    repo = _Repo([other], impl)
    st, _detail = assess_deleted_restore(repo, deleted, "42")
    assert st == DeletedPersonnelStatus.RESTORABLE


def test_assess_matricule_conflict_different_agents() -> None:
    deleted = _row(id="42", access_join_id="LEG-A", matricule_militaire="MM1")
    other = _row(id="77", access_join_id="LEG-B", matricule_militaire="MM1", noms="AUTRE")
    impl = _Impl(agent_by_career={"77": 2}, legacy_agent={})
    repo = _Repo([other], impl)
    st, detail = assess_deleted_restore(repo, deleted, "42")
    assert st == DeletedPersonnelStatus.MATRICULE_CONFLICT
    assert "AUTRE" in detail
