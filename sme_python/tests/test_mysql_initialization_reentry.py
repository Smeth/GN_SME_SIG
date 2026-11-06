from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories import mysql_connection, mysql_common
from sme_gend.services import mysql_import_period


@pytest.fixture(autouse=True)
def synthetic_maintenance_barrier(monkeypatch):
    monkeypatch.setattr('sme_gend.services.database_maintenance.coordinated_write', lambda *a, **kw: nullcontext())


def settings():
    return Settings(database_path=Path("synthetic.mdb"), mysql_host="invalid.test", mysql_database="synthetic")


def test_nested_initialization_uses_one_session_and_releases_only_at_outer_exit(monkeypatch):
    connections = []
    def connect(**kwargs):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        connections.append(connection)
        return connection
    monkeypatch.setattr(mysql_connection.pymysql, "connect", connect)
    with mysql_connection.mysql_initialization_lock(settings()):
        with mysql_connection.mysql_initialization_lock(settings()):
            assert len(connections) == 1
        cursor = connections[0].cursor.return_value.__enter__.return_value
        assert cursor.execute.call_count == 1
        connections[0].close.assert_not_called()
    assert cursor.execute.call_count == 2
    connections[0].close.assert_called_once()


def test_initialization_scope_does_not_reuse_another_database_lock(monkeypatch):
    connections = []
    def connect(**kwargs):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        connections.append(connection)
        return connection
    monkeypatch.setattr(mysql_connection.pymysql, "connect", connect)
    with mysql_connection.mysql_initialization_lock(settings()):
        with mysql_connection.mysql_initialization_lock(replace(settings(), mysql_database="other")):
            assert len(connections) == 2
    assert all(connection.close.call_count == 1 for connection in connections)


def schema_mocks(monkeypatch):
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
    class DB:
        def __init__(self, *args):
            pass
        @contextmanager
        def connect(self, *, read_only=False):
            yield connection
    monkeypatch.setattr(mysql_common, "MySQLConnection", DB)
    monkeypatch.setattr(mysql_common, "mysql_initialization_lock", lambda settings: nullcontext())
    monkeypatch.setattr(mysql_common, "_schema_ready_for", set())
    monkeypatch.setattr(mysql_common, "_schema_init_lock", threading.RLock())


def test_repository_constructed_during_migration_does_not_restart_migration(monkeypatch):
    schema_mocks(monkeypatch)
    calls = []
    def migration(config):
        calls.append(config)
        if len(calls) > 1:
            raise RuntimeError("recursive migration detected")
        mysql_common.ensure_mysql_schema(config)
    monkeypatch.setattr(mysql_import_period, "_ensure_mysql_migrations_unlocked", migration)
    mysql_common.ensure_mysql_schema(settings())
    assert len(calls) == 1


def test_failed_migration_can_retry_without_marking_schema_ready(monkeypatch):
    schema_mocks(monkeypatch)
    migrate = MagicMock(side_effect=[RuntimeError("synthetic migration failure"), None])
    monkeypatch.setattr(mysql_import_period, "_ensure_mysql_migrations_unlocked", migrate)
    with pytest.raises(RuntimeError, match="synthetic"):
        mysql_common.ensure_mysql_schema(settings())
    assert mysql_connection.mysql_database_key(settings()) not in mysql_common._schema_ready_for
    mysql_common.ensure_mysql_schema(settings())
    assert migrate.call_count == 2


def test_seed_failure_is_not_silently_marked_success(monkeypatch):
    connection = MagicMock()
    @contextmanager
    def connect(self, *, read_only=False):
        yield connection
    monkeypatch.setattr(mysql_import_period.MySQLConnection, "connect", connect)
    repo = MagicMock()
    repo.seed_defaults_if_empty.side_effect = RuntimeError("synthetic seed failure")
    monkeypatch.setattr("sme_gend.repositories.mysql_distinction_repository.MySQLDistinctionRepository", lambda config: repo)
    with pytest.raises(RuntimeError, match="distinction"):
        mysql_import_period.apply_agent_distinction_schema(settings())
