from contextlib import contextmanager
from pathlib import Path

import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories import mysql_connection as mysql


class Connection:
    server_status = 0
    def __init__(self):
        self.statements = []
        self.rollbacks = 0
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, query):
        self.statements.append(query)
    def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize("failure", [False, True])
def test_shared_snapshot_has_explicit_isolation_and_always_ends(monkeypatch, failure):
    settings = Settings(database_path=Path("unused"))
    connection = Connection()
    @contextmanager
    def connect(self, *, read_only=False):
        assert read_only
        yield connection
    monkeypatch.setattr(mysql.MySQLConnection, "connect", connect)
    try:
        with mysql.mysql_read_snapshot(settings) as shared:
            assert mysql.has_import_transaction(settings)
            mysql.transaction_local_cache("probe")["value"] = 1
            with mysql.mysql_read_snapshot(settings) as nested:
                assert nested is shared
                assert mysql.transaction_local_cache("probe")["value"] == 1
            shared.commit()  # Repository commits cannot end the snapshot.
            if failure:
                raise ValueError("load failed")
    except ValueError:
        assert failure
    assert connection.statements == ["SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                                     "START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY"]
    assert connection.rollbacks == 1
    assert not mysql.has_import_transaction(settings)
    assert mysql.current_transaction_settings() is None
    assert mysql.transaction_local_cache("probe") is None


def test_snapshot_cannot_publish_uncommitted_business_data():
    settings = Settings(database_path=Path("unused"))
    token = mysql._import_transaction.set((mysql.mysql_database_key(settings), Connection()))
    try:
        with pytest.raises(RuntimeError, match="transaction en cours"):
            with mysql.mysql_read_snapshot(settings):
                pytest.fail("entered an uncommitted transaction")
    finally:
        mysql._import_transaction.reset(token)
