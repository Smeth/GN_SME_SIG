"""project_carriere_from_dossier_batch : le référentiel doit être chargé une seule
fois pour tout le lot, pas une fois par agent (régression de performance réelle :
avec plusieurs centaines d'agents, un rechargement par agent pouvait faire durer
un import plus d'une heure)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import sot_maintenance as sm


class _FakeCursor:
    def execute(self, *_a, **_k) -> None:
        pass

    def fetchall(self):
        return [(10, 100), (11, 200), (12, 300)]

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


class _FakeConn:
    def cursor(self):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> bool:
        return False


class _FakeMySQLConnection:
    def __init__(self, settings: Settings) -> None:
        pass

    def connect(self):
        return _FakeConn()


class _FakeRepo:
    def __init__(self, settings: Settings) -> None:
        pass

    def sync_carriere_from_dossier(
        self, agent_id: int, carriere_id: str, *, ref_cache=None, flat_rows=None
    ) -> None:
        _sync_calls.append((agent_id, carriere_id, ref_cache, flat_rows))


_sync_calls: list[tuple[int, str, object, object]] = []


def test_project_carriere_from_dossier_batch_loads_ref_cache_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import contextmanager
    @contextmanager
    def transaction_boundary(*args,**kwargs):
        yield
    monkeypatch.setattr("sme_gend.services.automatic_maintenance.maintenance_operation",transaction_boundary)
    _sync_calls.clear()
    ref_cache_calls: list[int] = []
    flat_rows_calls: list[int] = []

    monkeypatch.setattr(sm, "MySQLConnection", _FakeMySQLConnection)
    monkeypatch.setattr(sm, "_active_mois", lambda: "2026-05")
    monkeypatch.setattr(
        "sme_gend.repositories.mysql_agent_dossier_repository.MySQLAgentDossierRepository",
        _FakeRepo,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_ref_etl.load_ref_cache_from_db",
        lambda cur: ref_cache_calls.append(1) or "CACHE_SENTINEL",
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_ref_etl.load_subdiv_flat_rows",
        lambda cur: flat_rows_calls.append(1) or "FLAT_SENTINEL",
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda _s: None,
    )

    settings = Settings(database_path=Path("."))
    result = sm.project_carriere_from_dossier_batch(settings, only_est_actuelle=True)

    assert result["projected"] == 3
    assert len(ref_cache_calls) == 1, "le référentiel doit être chargé une seule fois pour tout le lot"
    assert len(flat_rows_calls) == 1
    assert len(_sync_calls) == 3
    assert all(c[2] == "CACHE_SENTINEL" for c in _sync_calls), "même cache réutilisé pour chaque agent"
    assert all(c[3] == "FLAT_SENTINEL" for c in _sync_calls)
