"""Tests des caches et verrous d'initialisation MySQL."""

from __future__ import annotations

import threading
import pytest
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.services import mysql_import_period
from sme_gend.repositories import mysql_common
from sme_gend.repositories import mysql_connection
from sme_gend.services import app_config_store


@pytest.fixture(autouse=True)
def synthetic_maintenance_barrier(monkeypatch):
    monkeypatch.setattr('sme_gend.services.database_maintenance.coordinated_write', lambda *a, **kw: nullcontext())


class _Cursor:
    def __init__(self, statements: list[tuple[str, tuple | None]]) -> None:
        self.statements = statements
        self._result = (1,)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement: str, params: tuple | None = None) -> None:
        self.statements.append((statement, params))
        self._result = (1,)

    def fetchone(self):
        return self._result


class _Connection:
    def __init__(self, statements: list[tuple[str, tuple | None]]) -> None:
        self.statements = statements
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self.statements)

    def close(self) -> None:
        self.closed = True


def _settings() -> Settings:
    return Settings(
        database_path=Path("test.mdb"),
        mysql_host="db.example",
        mysql_database="sme_test",
        mysql_user="test",
    )


def test_initialization_lock_releases_mysql_advisory_lock(monkeypatch) -> None:
    statements: list[tuple[str, tuple | None]] = []
    connections = []
    connect_params: list[dict] = []

    def connect(**params):
        connect_params.append(params)
        conn = _Connection(statements)
        connections.append(conn)
        return conn

    monkeypatch.setattr(mysql_connection.pymysql, "connect", connect)

    with mysql_connection.mysql_initialization_lock(_settings()):
        assert len(connections) == 2
        assert all(not conn.closed for conn in connections)

    assert connect_params[0]["autocommit"] is True
    assert [statement for statement, _ in statements] == [
        "SELECT GET_LOCK(%s,%s)",
        "SELECT GET_LOCK(%s, %s)",
        "SELECT RELEASE_LOCK(%s)",
        "SELECT RELEASE_LOCK(%s)",
    ]
    assert statements[0][1][0].startswith('sme_schema_')
    assert statements[1][1][0].startswith('sme_gend_init_')
    assert statements[2][1][0] == statements[1][1][0]
    assert statements[3][1][0] == statements[0][1][0]
    assert all(conn.closed for conn in connections)


def test_schema_migrations_cached_across_view_months(monkeypatch) -> None:
    settings = _settings()
    archive_settings = replace(settings, mysql_view_mois="2026-04")
    statements: list[tuple[str, tuple | None]] = []
    migrations: list[Settings] = []

    class _MySQLConnection:
        def __init__(self, _settings: Settings) -> None:
            pass

        @contextmanager
        def connect(self, *, read_only=False):
            yield _Connection(statements)

    monkeypatch.setattr(mysql_common, "_schema_ready_for", set())
    monkeypatch.setattr(mysql_common, "_schema_init_lock", threading.RLock())
    monkeypatch.setattr(mysql_common, "MySQLConnection", _MySQLConnection)
    monkeypatch.setattr(
        mysql_common, "mysql_initialization_lock", lambda _settings: nullcontext()
    )
    monkeypatch.setattr(
        mysql_import_period,
        "_ensure_mysql_migrations_unlocked",
        migrations.append,
    )

    mysql_common.ensure_mysql_schema(settings)
    mysql_common.ensure_mysql_schema(archive_settings)

    assert migrations == [settings]


def test_central_config_initialized_once_per_database(monkeypatch) -> None:
    settings = _settings()
    archive_settings = replace(settings, mysql_view_mois="2026-04")
    calls: list[str] = []

    class _Repository:
        def __init__(self, _settings: Settings) -> None:
            pass

        def ensure_tables(self) -> None:
            calls.append("tables")

    monkeypatch.setattr(app_config_store, "_ensure_config_done", set())
    monkeypatch.setattr(
        app_config_store, "mysql_initialization_lock", lambda _settings: nullcontext()
    )
    monkeypatch.setattr(app_config_store, "_repo", _Repository)
    monkeypatch.setattr(
        app_config_store, "migrate_json_files_to_database", lambda _settings: calls.append("json")
    )
    monkeypatch.setattr(
        "sme_gend.services.config_migration_v2.run_migration_v2",
        lambda _settings: calls.append("v2"),
    )

    app_config_store.ensure_central_config_ready(settings)
    app_config_store.ensure_central_config_ready(archive_settings)

    assert calls == ["tables", "json", "v2"]
