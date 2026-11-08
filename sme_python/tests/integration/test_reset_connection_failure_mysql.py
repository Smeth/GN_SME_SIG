"""Real disconnection in a disposable database, never on the user's base."""
from concurrent.futures import ThreadPoolExecutor

import pymysql
import pytest
from test_restore_mysql import isolated_mysql
from test_application_reset_mysql import prepare,make_backup
from sme_gend.services.application_reset import reset_application,ResetOptions,reset_target


def test_reset_keeps_the_real_query_timeout_and_rolls_back_all_data(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.repositories import mysql_pool as pools
    settings,connect,params,old_auth,local=prepare(isolated_mysql,monkeypatch,tmp_path)
    original_new=pools._new_connection
    def short_write_timeout(settings,*,read_only):
        connection=original_new(settings,read_only=read_only)
        if not read_only:
            connection._read_timeout=.1
        return connection
    monkeypatch.setattr(pools,'_new_connection',short_write_timeout)
    def safety_then_slow_delete(settings):
        path=make_backup(settings,backup,tmp_path)
        # Deliberate server-side delay AFTER the valid safety dump, to model a
        # large/blocked DELETE exceeding the client deadline.
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('CREATE TRIGGER synthetic_slow_reset BEFORE DELETE ON audit_journal '
                               'FOR EACH ROW DO SLEEP(0.5)')
        return path
    monkeypatch.setattr(backup,'create_backup_to_default_dir',safety_then_slow_delete)
    with ThreadPoolExecutor(max_workers=1) as workers:
        operation=workers.submit(reset_application,settings,ResetOptions(),confirmation=reset_target(settings))
        with pytest.raises(pymysql.OperationalError) as raised:
            operation.result(timeout=30)
    assert raised.value.args[0]==2013
    assert raised.value.reset_stage=='suppression de la table audit_journal'
    assert local.read_bytes()==b'OLD MONTH'
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent')
            assert cursor.fetchone()[0]>0
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE action='application_reset'")
            assert cursor.fetchone()[0]==0
            cursor.execute('SELECT active FROM _sme_maintenance_state WHERE id=1')
            assert cursor.fetchone()[0]==0


def test_slow_table_succeeds_in_batches_below_the_statement_deadline(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services import application_reset as reset,backup_restore_service as backup
    from sme_gend.repositories import mysql_pool as pools
    settings,connect,params,old_auth,local=prepare(isolated_mysql,monkeypatch,tmp_path)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.executemany("INSERT INTO audit_journal(ts_utc,action,module,payload) VALUES(UTC_TIMESTAMP(),'synthetic','personnel','{}')",
                               [() for _ in range(20)])
    monkeypatch.setattr(reset,'_RESET_DELETE_BATCH_SIZE',4)
    original_new=pools._new_connection
    def short_write_timeout(settings,*,read_only):
        connection=original_new(settings,read_only=read_only)
        if not read_only:connection._read_timeout=.25
        return connection
    monkeypatch.setattr(pools,'_new_connection',short_write_timeout)
    def safety_then_slow_delete(settings):
        path=make_backup(settings,backup,tmp_path)
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('CREATE TRIGGER synthetic_slow_reset BEFORE DELETE ON audit_journal '
                               'FOR EACH ROW DO SLEEP(0.02)')
        return path
    monkeypatch.setattr(backup,'create_backup_to_default_dir',safety_then_slow_delete)
    with ThreadPoolExecutor(max_workers=1) as workers:
        result=workers.submit(reset_application,settings,ResetOptions(),confirmation=reset_target(settings)).result(timeout=30)
    assert result['removed']['audit_journal']==21
    assert not local.exists()
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent')
            assert cursor.fetchone()[0]==0
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE module='doc_gen'")
            assert cursor.fetchone()[0]==1
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE action='application_reset'")
            assert cursor.fetchone()[0]==1


def test_lost_commit_acknowledgement_keeps_maintenance_and_never_replays_reset(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories import mysql_pool as pools
    settings,connect,params,old_auth,local=prepare(isolated_mysql,monkeypatch,tmp_path)
    original_new=pools._new_connection
    commits=[]
    def uncertain_write_commit(settings,*,read_only):
        connection=original_new(settings,read_only=read_only)
        if not read_only:
            original_commit=connection.commit
            def commit_then_disconnect():
                original_commit()
                commits.append('committed')
                connection.close()
                raise pymysql.OperationalError(2013,'Synthetic lost commit acknowledgement')
            connection.commit=commit_then_disconnect
        return connection
    monkeypatch.setattr(pools,'_new_connection',uncertain_write_commit)
    with ThreadPoolExecutor(max_workers=1) as workers:
        result=workers.submit(reset_application,settings,ResetOptions(),confirmation=reset_target(settings))
        with pytest.raises(pymysql.OperationalError) as raised:result.result(timeout=30)
    assert raised.value.reset_commit_uncertain and commits==['committed']
    assert not local.exists()  # Recovery files are retained instead of a guessed rollback.
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent')
            assert cursor.fetchone()[0]==0
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE action='application_reset'")
            assert cursor.fetchone()[0]==1
            cursor.execute('SELECT active FROM _sme_maintenance_state WHERE id=1')
            assert cursor.fetchone()[0]==1


def test_failed_file_rollback_reports_recovery_and_keeps_maintenance(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.application_reset_files import ResetLocalFiles
    settings,connect,params,old_auth,local=prepare(isolated_mysql,monkeypatch,tmp_path)
    def failed_audit(*a,**kw):raise RuntimeError('synthetic original audit failure')
    def failed_recovery(*a):raise OSError('synthetic file recovery failure')
    monkeypatch.setattr(AppConfigRepository,'append_audit',failed_audit)
    monkeypatch.setattr(ResetLocalFiles,'rollback',failed_recovery)
    with pytest.raises(OSError) as raised:
        reset_application(settings,ResetOptions(),confirmation=reset_target(settings))
    assert raised.value.reset_recovery_required
    assert raised.value.reset_stage=='récupération des fichiers'
    assert isinstance(raised.value.__cause__,RuntimeError)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent')
            assert cursor.fetchone()[0]>0
            cursor.execute('SELECT active FROM _sme_maintenance_state WHERE id=1')
            assert cursor.fetchone()[0]==1


def test_fk_cleanup_failure_inside_a_callers_except_still_rolls_back(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories import mysql_pool as pools
    settings,connect,params,old_auth,local=prepare(isolated_mysql,monkeypatch,tmp_path)
    pools.invalidate_mysql_pool(settings)
    original_new=pools._new_connection
    def fail_fk_restore(settings,*,read_only):
        connection=original_new(settings,read_only=read_only)
        if not read_only:
            original_cursor=connection.cursor
            def cursor(*a,**kw):
                result=original_cursor(*a,**kw)
                original_execute=result.execute
                def execute(sql,args=None):
                    if sql=='SET SESSION FOREIGN_KEY_CHECKS=1':
                        raise RuntimeError('synthetic FK restoration failure')
                    return original_execute(sql,args)
                result.execute=execute
                return result
            connection.cursor=cursor
        return connection
    monkeypatch.setattr(pools,'_new_connection',fail_fk_restore)
    try:
        raise ValueError('synthetic caller exception')
    except ValueError:
        with pytest.raises(RuntimeError,match='FK restoration'):
            reset_application(settings,ResetOptions(),confirmation=reset_target(settings))
    assert local.exists()
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM agent')
            assert cursor.fetchone()[0]>0
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE action='application_reset'")
            assert cursor.fetchone()[0]==0
