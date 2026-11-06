"""Only owned schemas: real backup/DROP, recovery, refusal and partial outcomes."""
from pathlib import Path
import json
import pytest
from test_automatic_maintenance_mysql import setup
from test_restore_mysql import isolated_mysql
from sme_gend.services import legacy_cleanup as cleanup
from sme_gend.services.data_sync import read_data_revision


def seed(isolated_mysql,monkeypatch):
    settings,connect,params=setup(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE tab_structure(id BIGINT AUTO_INCREMENT PRIMARY KEY,txt TEXT,bin BLOB) ENGINE=InnoDB')
            cur.execute('CREATE TABLE tab_grade(id BIGINT PRIMARY KEY,txt TEXT) ENGINE=InnoDB')
            cur.execute('INSERT INTO tab_structure(id,txt,bin) VALUES(%s,%s,%s)',(5,"SYNTHETIC \u00e9\n'\\;",b'\x00\xff'))
            cur.execute("INSERT INTO tab_grade VALUES(0,'SYNTHETIC GRADE')")
    return settings,connect,params


def remaining(connect,params):
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('tab_structure','tab_grade') ORDER BY TABLE_NAME")
            return [row[0] for row in cur.fetchall()]


def test_legacy_lot31_verified_backup_drop_and_actual_recovery(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.services.backup_restore_service import _split_sql_statements
    settings,connect,params=seed(isolated_mysql,monkeypatch)
    plan=cleanup.prepare_legacy_cleanup(settings);before=read_data_revision(settings)
    report=cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    assert report['phase']=='complete' and report['notification_published']
    assert not remaining(connect,params) and read_data_revision(settings)!=before
    assert Path(report['backup']).is_file() and json.loads(Path(report['journal']).read_text(encoding='utf-8'))['phase']=='complete'
    # Restore only the writer-generated backup, only into this fixture's
    # disposable database, after verifying the original DB and directory.
    backup=Path(report['backup']);assert backup.resolve().is_relative_to(tmp_path.resolve())
    assert params['database'].startswith('impression_test_')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for statement in _split_sql_statements(backup.read_text(encoding='utf-8')):cur.execute(statement)
    assert cleanup.prepare_legacy_cleanup(settings).state==plan.state


@pytest.mark.parametrize('missing',['maintenance','backup','plan'])
def test_legacy_lot31_missing_prerequisite_never_drops(isolated_mysql,monkeypatch,tmp_path,missing):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    with pytest.raises(ValueError):
        cleanup.run_legacy_cleanup(settings,backup_dir=None if missing=='backup' else tmp_path,
            maintenance_confirmed=missing!='maintenance',expected=None if missing=='plan' else plan)
    assert remaining(connect,params)==['tab_grade','tab_structure']


def test_legacy_lot31_changed_table_refuses_old_plan(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute("UPDATE tab_grade SET txt='OTHER COMMIT'")
    with pytest.raises(ValueError,match='change'):
        cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    assert len(remaining(connect,params))==2


def test_legacy_lot31_backup_failure_never_drops(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    def fail(*args,**kwargs):raise OSError('synthetic backup failure')
    monkeypatch.setattr(cleanup,'_python_sql_dump',fail)
    with pytest.raises(cleanup.LegacyCleanupError) as error:
        cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    assert error.value.report['tables_dropped']==[] and len(remaining(connect,params))==2


def test_legacy_lot31_notification_failure_reports_completed_ddl(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings);before=read_data_revision(settings)
    def fail(*args,**kwargs):raise RuntimeError('synthetic signal failure')
    monkeypatch.setattr('sme_gend.services.data_sync.publish_data_change',fail)
    with pytest.raises(cleanup.LegacyCleanupError) as error:
        cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    report=error.value.report
    assert report['tables_dropped']==['tab_grade','tab_structure'] and not report['notification_published']
    from sme_gend.services.database_maintenance import maintenance_status
    assert maintenance_status(settings)['active']
    assert not remaining(connect,params) and read_data_revision(settings)==before and Path(report['backup']).is_file()


def test_legacy_lot31_partial_ddl_is_described_honestly(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    def partial(cur,names):cur.execute('DROP TABLE `tab_grade`');raise RuntimeError('synthetic lost acknowledgement')
    monkeypatch.setattr(cleanup,'_execute_drop',partial)
    with pytest.raises(cleanup.LegacyCleanupError) as error:
        cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    report=error.value.report
    assert report['outcome_known'] and report['tables_dropped']==['tab_grade'] and report['tables_remaining']==['tab_structure']
    from sme_gend.services.database_maintenance import maintenance_status
    assert maintenance_status(settings)['active']
    assert Path(report['backup']).is_file() and not report['notification_published']


def test_legacy_lot31_another_open_client_blocks_drop(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    other=connect(**params)
    try:
        with pytest.raises(RuntimeError,match='autres postes'):
            cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    finally:other.close()
    assert len(remaining(connect,params))==2


def test_legacy_lot31_drop_never_commits_a_business_transaction(isolated_mysql,monkeypatch,tmp_path):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction,MySQLConnection
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    with pytest.raises(RuntimeError,match='DDL interdit'):
        with mysql_import_transaction(settings):
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:cur.execute("UPDATE ref_structure SET designation='UNCOMMITTED'")
            cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:cur.execute('SELECT designation FROM ref_structure');assert cur.fetchone()==('ORIGINAL',)
    assert len(remaining(connect,params))==2



def test_legacy_lot31_active_foreign_key_blocks_drop(isolated_mysql,monkeypatch):
    settings,connect,params=seed(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('CREATE TABLE synthetic_active(id BIGINT PRIMARY KEY,legacy_id BIGINT,FOREIGN KEY(legacy_id) REFERENCES tab_structure(id)) ENGINE=InnoDB')
    with pytest.raises(RuntimeError,match='referencent'):cleanup.prepare_legacy_cleanup(settings)
    assert len(remaining(connect,params))==2


def test_legacy_lot31_unprotectable_backup_directory_blocks_drop(isolated_mysql,monkeypatch,tmp_path):
    settings,connect,params=seed(isolated_mysql,monkeypatch);plan=cleanup.prepare_legacy_cleanup(settings)
    def fail(*a,**kw):raise OSError('synthetic ACL failure')
    monkeypatch.setattr(cleanup,'_restrict_folder',fail)
    with pytest.raises(RuntimeError,match='proteger'):
        cleanup.run_legacy_cleanup(settings,backup_dir=tmp_path,maintenance_confirmed=True,expected=plan)
    assert len(remaining(connect,params))==2 and not list(tmp_path.glob('legacy_cleanup_*/before_drop.sql'))
