from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import date

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple | None]] = []
        self.lastrowid = 88

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.calls.append((query, params))

    def fetchone(self):
        if len(self.calls) == 1:
            return (41, "LEG-41")
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args) -> None:
        return None

    @contextmanager
    def cursor(self):
        yield self._cursor


def test_delete_row_with_perte_records_movement_and_keeps_agent(monkeypatch) -> None:
    cursor = _Cursor()
    repository = MySQLAgentRepository.__new__(MySQLAgentRepository)
    repository._settings = object()
    monkeypatch.setattr("sme_gend.services.personnel_service._personnel_write", lambda *a, **kw: nullcontext())
    monkeypatch.setattr("sme_gend.repositories.write_guard.lock_agent", lambda *a, **kw: 41)
    monkeypatch.setattr(repository, "_connect", lambda: _Connection(cursor))
    row = ListeGeneraleRow(
        id="12",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="ADC",
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
        access_join_id="LEG-41",
    )

    repository.delete_row_with_perte(row, mois_ym="2026-10", motif="RETRAITE")

    movement_insert = next(
        (sql, params)
        for sql, params in cursor.calls
        if "INSERT INTO mouvement_effectif" in sql
    )
    assert movement_insert[1] == (
        88,
        41,
        "LEG-41",
        "ADC",
        "DUPONT Jean",
        "PERTE",
        "PERTE",
        "RETRAITE",
        "102026",
        "RETRAITE",
        date(2026, 10, 31),
    )
    close_update = next(
        (sql, params)
        for sql, params in cursor.calls
        if "UPDATE carriere_situation" in sql and "date_fin" in sql
    )
    assert close_update[1][-1] == 12
    assert close_update[1][-2] == date(2026, 10, 31)
    assert not any("DELETE FROM agent " in sql for sql, _params in cursor.calls)
