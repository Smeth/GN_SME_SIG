from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager, nullcontext
from unittest.mock import MagicMock

import pytest

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import personnel_service
from sme_gend.services import padmin_satellite_apply as apply


def _row(row_id: str = "1") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id,
        noms=f"Agent {row_id}",
        date_naissance=None,
        lieu_naissance=None,
        grade="MDL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
        access_join_id=f"LEG-{row_id}",
    )


class _Personnel:
    def __init__(self, _settings) -> None:
        self.updates: list[tuple[ListeGeneraleRow, ListeGeneraleRow, str | None]] = []
        self.persisted = {}

    def update_personnel(
        self,
        before: ListeGeneraleRow,
        after: ListeGeneraleRow,
        *,
        edit_motif: str | None = None,
    ) -> None:
        self.updates.append((before, after, edit_motif))
        self.persisted[after.id] = after


@pytest.fixture
def bulk_dependencies(monkeypatch):
    monkeypatch.setattr(apply,"position_edit",lambda *a,**kw:nullcontext())
    monkeypatch.setattr(apply, "_load_bulk_snapshots", lambda sat,kinds:{kind:{} for kind in kinds})
    personnel = _Personnel(None)
    @contextmanager
    def transaction(*args, **kwargs):
        previous = dict(personnel.persisted)
        try:
            yield
        except Exception:
            personnel.persisted.clear()
            personnel.persisted.update(previous)
            raise
    monkeypatch.setattr(personnel_service, "_personnel_write", transaction)
    monkeypatch.setattr(personnel_service, "PersonnelService", lambda settings: personnel)
    monkeypatch.setattr(
        apply.BulkMissionStageDialog,
        "run",
        staticmethod(lambda _parent, *, count, nature_suggestions=None: ("mission", object())),
    )
    monkeypatch.setattr(apply, "NumeriqueSatelliteRepository", lambda _settings: object())
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _settings: None,
    )
    return personnel


def test_bulk_update_uses_personnel_audit_service(
    monkeypatch, bulk_dependencies
) -> None:
    row = _row()
    monkeypatch.setattr(apply, "_apply_form", lambda *_args, **_kwargs: None)

    result = apply.bulk_apply_mission_or_stage(object(), object(), [row])

    assert result == (1, [])
    assert len(bulk_dependencies.updates) == 1
    before, after, motif = bulk_dependencies.updates[0]
    assert before == row
    assert after == replace(row, position_admin="Mission")
    assert motif == "Affectation groupée de position administrative."


def test_bulk_satellite_failure_restores_previous_position(
    monkeypatch, bulk_dependencies
) -> None:
    row = _row()

    def fail_satellite_write(*_args, **_kwargs) -> None:
        raise RuntimeError("échec MySQL")

    monkeypatch.setattr(apply, "_apply_form", fail_satellite_write)

    count, errors = apply.bulk_apply_mission_or_stage(object(), object(), [row])

    assert count == 0
    assert len(bulk_dependencies.updates) == 1
    assert bulk_dependencies.persisted == {}
    assert "enregistrement annulé" in errors[0]
    assert "échec MySQL" in errors[0]


def test_bulk_position_failure_never_writes_satellite(monkeypatch, bulk_dependencies) -> None:
    def fail_position(*args, **kwargs):
        raise RuntimeError("synthetic position failure")
    bulk_dependencies.update_personnel = fail_position
    writer = MagicMock()
    monkeypatch.setattr(apply, "_apply_form", writer)
    count, errors = apply.bulk_apply_mission_or_stage(object(), object(), [_row()])
    assert count == 0 and "synthetic position failure" in errors[0]
    writer.assert_not_called()
    assert bulk_dependencies.persisted == {}
