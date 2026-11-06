from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories import mysql_common


@pytest.mark.parametrize("actual, requested", [("isolated_target", "isolated_target"), ("isolated_target", "ISOLATED_TARGET")])
def test_schema_never_switches_to_embedded_database(tmp_path, monkeypatch, actual, requested):
    sql = tmp_path / "schema.sql"
    sql.write_text("USE smegend;\nCREATE TABLE example (id INT);\n", encoding="utf-8")
    cursor = MagicMock()
    cursor.fetchone.return_value = (actual, 1)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    @contextmanager
    def connect():
        yield conn
    monkeypatch.setattr(mysql_common, "mysql_schema_file", lambda: sql)
    monkeypatch.setattr(mysql_common, "MySQLConnection", lambda settings: SimpleNamespace(connect=connect))
    mysql_common.apply_mysql_schema(Settings(database_path=Path("unused"), mysql_database=requested))
    statements = [str(call.args[0]).strip().upper() for call in cursor.execute.call_args_list]
    assert not any(statement.startswith("USE ") for statement in statements)
    assert any(statement.startswith("CREATE TABLE") for statement in statements)


def test_schema_rejects_connection_already_pointing_to_another_database(tmp_path, monkeypatch):
    sql = tmp_path / "schema.sql"
    sql.write_text("CREATE TABLE example (id INT);", encoding="utf-8")
    cursor = MagicMock()
    cursor.fetchone.return_value = ("wrong_database",)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    @contextmanager
    def connect():
        yield conn
    monkeypatch.setattr(mysql_common, "mysql_schema_file", lambda: sql)
    monkeypatch.setattr(mysql_common, "MySQLConnection", lambda settings: SimpleNamespace(connect=connect))
    with pytest.raises(RuntimeError):
        mysql_common.apply_mysql_schema(Settings(database_path=Path("unused"), mysql_database="isolated_target"))
    assert not any(str(call.args[0]).startswith("CREATE") for call in cursor.execute.call_args_list)
