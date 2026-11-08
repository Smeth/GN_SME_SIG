"""A closed MySQL socket must not erase the failure that caused its cleanup."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pymysql
import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories import mysql_connection as mysql
from sme_gend.services import database_maintenance as maintenance
from sme_gend.services import mysql_backup_service as backup


def settings():
    return Settings(database_path=Path('unused'),mysql_host='synthetic.invalid',mysql_database='cleanup_synthetic')


class ControlConnection:
    def __init__(self, *, release_error=None, close_error=None):
        self.open=True
        self.release_error=release_error
        self.close_error=close_error
        self.closed=False
        self.statements=[]

    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=()):
        self.statements.append(sql)
        if 'RELEASE_LOCK' in sql and self.release_error is not None:raise self.release_error
    def fetchone(self):return (1,)
    def close(self):
        self.closed=True
        self.open=False
        if self.close_error is not None:raise self.close_error


@pytest.mark.parametrize('lock',[mysql.schema_metadata_lock,mysql._mysql_initialization_lock])
def test_control_lock_preserves_query_failure_when_release_and_close_also_fail(monkeypatch,lock):
    original=pymysql.OperationalError(2013,'synthetic lost connection during query')
    connection=ControlConnection(release_error=pymysql.InterfaceError(0,''),close_error=OSError('synthetic close'))
    connect=Mock(return_value=connection)
    monkeypatch.setattr(mysql.pymysql,'connect',connect)
    monkeypatch.setattr(maintenance,'coordinated_write',lambda *a,**kw:nullcontext())
    with pytest.raises(pymysql.OperationalError) as caught:
        with lock(settings()):raise original
    assert caught.value is original
    assert connection.closed and connect.call_count==1


@pytest.mark.parametrize('lock',[mysql.schema_metadata_lock,mysql._mysql_initialization_lock])
def test_control_lock_does_not_report_success_if_release_fails(monkeypatch,lock):
    failure=pymysql.InterfaceError(0,'')
    connection=ControlConnection(release_error=failure)
    monkeypatch.setattr(mysql.pymysql,'connect',lambda **kw:connection)
    monkeypatch.setattr(maintenance,'coordinated_write',lambda *a,**kw:nullcontext())
    with pytest.raises(pymysql.InterfaceError) as caught:
        with lock(settings()):pass
    assert caught.value is failure and connection.closed


@pytest.mark.parametrize('lock',[mysql.schema_metadata_lock,mysql._mysql_initialization_lock])
def test_control_lock_requires_live_ownership_after_a_successful_body(monkeypatch,lock):
    connection=ControlConnection()
    monkeypatch.setattr(mysql.pymysql,'connect',lambda **kw:connection)
    monkeypatch.setattr(maintenance,'coordinated_write',lambda *a,**kw:nullcontext())
    with pytest.raises(RuntimeError,match='perdue'):
        with lock(settings()):connection.open=False
    assert not any('RELEASE_LOCK' in statement for statement in connection.statements)


@pytest.mark.parametrize('body_failure',[False,True])
def test_maintenance_cleanup_failure_preserves_body_error_and_retains_lease(monkeypatch,body_failure):
    original=pymysql.OperationalError(2013,'synthetic query timeout')
    cleanup_error=pymysql.InterfaceError(0,'')
    connection=ControlConnection()
    execute=connection.execute
    def run(sql,params=()):
        if 'SET active=0' in sql:raise cleanup_error
        return execute(sql,params)
    connection.execute=run
    monkeypatch.setattr(mysql.pymysql,'connect',lambda **kw:connection)
    monkeypatch.setattr(maintenance,'_ensure',lambda c:None)
    monkeypatch.setattr(maintenance,'_state',lambda c:{'active':False,'token':''})
    monkeypatch.setattr(maintenance,'_wait_for_writers',lambda *a,**kw:None)
    monkeypatch.setattr(maintenance,'_scalar',lambda c,sql,params=():None if 'IS_USED_LOCK' in sql else 1)
    expected=original if body_failure else cleanup_error
    with pytest.raises(type(expected)) as caught:
        with maintenance.centralized_maintenance(settings()) as lease:
            if body_failure:raise original
    assert caught.value is expected and lease.retained and connection.closed
    assert maintenance._owner.get() is None


def test_lost_maintenance_connection_never_reconnects_or_clears_active_state(monkeypatch):
    original=pymysql.OperationalError(2013,'synthetic unknown transaction outcome')
    connection=ControlConnection()
    connect=Mock(return_value=connection)
    monkeypatch.setattr(mysql.pymysql,'connect',connect)
    monkeypatch.setattr(maintenance,'_ensure',lambda c:None)
    monkeypatch.setattr(maintenance,'_state',lambda c:{'active':False,'token':''})
    monkeypatch.setattr(maintenance,'_wait_for_writers',lambda *a,**kw:None)
    monkeypatch.setattr(maintenance,'_scalar',lambda c,sql,params=():None if 'IS_USED_LOCK' in sql else 1)
    with pytest.raises(pymysql.OperationalError) as caught:
        with maintenance.centralized_maintenance(settings()) as lease:
            connection.open=False
            raise original
    assert caught.value is original and lease.retained
    assert connect.call_count==1
    assert not any('SET active=0' in sql for sql in connection.statements)


@pytest.mark.parametrize('body_failure',[False,True])
def test_dump_cleanup_preserves_original_timeout_and_is_strict_after_success(monkeypatch,tmp_path,body_failure):
    original=pymysql.OperationalError(2013,'synthetic timeout reading snapshot')
    cleanup_error=pymysql.InterfaceError(0,'')
    connection=MagicMock(server_status=0,open=True)
    cursor=connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value=()
    cursor.fetchone.return_value=(0,)
    connection.rollback.side_effect=[None,cleanup_error]
    @contextmanager
    def connect(self,*,read_only=False):
        assert read_only
        yield connection
    monkeypatch.setattr(mysql.MySQLConnection,'connect',connect)
    def snapshot(*a):
        if body_failure:raise original
    expected=original if body_failure else cleanup_error
    with pytest.raises(type(expected)) as caught:
        backup._python_sql_dump_locked(settings(),tmp_path/'synthetic.sql',snapshot_reader=snapshot)
    assert caught.value is expected
    assert connection.rollback.call_count==2
