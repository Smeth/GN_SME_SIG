from concurrent.futures import ThreadPoolExecutor
from threading import Event
from dataclasses import replace
import pytest
from test_restore_mysql import isolated_mysql
from test_sensitive_audit_mysql import journal
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.database_maintenance import (
    centralized_maintenance, maintenance_status, end_interrupted_maintenance, MaintenanceActiveError,
)


def test_maintenance_blocks_other_clients_and_preserves_read_only_access(journal):
    settings, _ = journal
    entered, release = Event(), Event()
    def owner():
        with centralized_maintenance(settings):
            entered.set()
            assert release.wait(15)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(owner)
        try:
            assert entered.wait(10)
            assert maintenance_status(settings)['active']
            with pytest.raises(MaintenanceActiveError):
                with MySQLConnection(replace(settings, mysql_view_mois='2026-08')).connect():
                    pytest.fail('Other client entered a write scope')
            with MySQLConnection(settings).connect(read_only=True) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT COUNT(*) FROM audit_journal')
                    assert cursor.fetchone()[0] == 0
            with pytest.raises(MaintenanceActiveError):
                end_interrupted_maintenance(settings, expected_token=maintenance_status(settings)['token'], recovery_verified=True)
        finally:
            release.set()
        pending.result(timeout=10)
    assert not maintenance_status(settings)['active']
    with MySQLConnection(settings).connect():
        pass


def test_interrupted_maintenance_remains_closed_until_verified_recovery(journal):
    settings, _ = journal
    with centralized_maintenance(settings) as lease:
        lease.keep_blocked()
    state = maintenance_status(settings)
    assert state['active']
    with pytest.raises(MaintenanceActiveError):
        with MySQLConnection(settings).connect():
            pytest.fail('Interrupted maintenance admitted a writer')
    with pytest.raises(ValueError):
        end_interrupted_maintenance(settings, expected_token=state['token'])
    with pytest.raises(RuntimeError):
        end_interrupted_maintenance(settings, expected_token='stale-token', recovery_verified=True)
    assert maintenance_status(settings)['active']
    end_interrupted_maintenance(settings, expected_token=state['token'], recovery_verified=True)
    assert not maintenance_status(settings)['active']
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM audit_journal WHERE action='maintenance_recovered'")
            assert cursor.fetchone()[0] == 1


def test_restore_keeps_live_audit_and_publishes_its_own_trace(journal, tmp_path):
    from sme_gend.services.mysql_restore_staging import prepared_restore
    settings, insert = journal
    insert(action='CURRENT_EVENT', module='system')
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    AppConfigRepository(settings).set_json('audit_mirror:current', {'pending':'CURRENT'})
    statements = ['CREATE TABLE sample (id INT PRIMARY KEY) ENGINE=InnoDB;', 'INSERT INTO sample VALUES (7);']
    workspace = tmp_path / 'restore'
    workspace.mkdir()
    with prepared_restore(settings, statements, workspace) as prepared:
        assert maintenance_status(settings)['active']
        prepared.validate()
        prepared.apply_config([{'config_key':'audit_mirror:old','payload':{'pending':'OLD'}}], None)
        prepared.record_restore_audit(archive_name='synthetic.zip', preserve_audit=True)
        prepared.publish()
    assert not maintenance_status(settings)['active']
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT action FROM audit_journal ORDER BY id')
            assert cursor.fetchall() == (('CURRENT_EVENT',), ('database_restored',))
            cursor.execute('SELECT id FROM sample')
            assert cursor.fetchone() == (7,)
            cursor.execute("SELECT config_key FROM app_config WHERE config_key LIKE 'audit_mirror:%%' ORDER BY config_key")
            assert cursor.fetchall() == (('audit_mirror:current',),)


def test_failed_switch_keeps_maintenance_until_explicit_recovery(journal, tmp_path):
    from sme_gend.services.mysql_restore_staging import prepared_restore
    settings, _ = journal
    workspace = tmp_path / 'recovery'
    workspace.mkdir()
    with pytest.raises(RuntimeError, match='SYNTHETIC'):
        with prepared_restore(settings, ['CREATE TABLE sample (id INT) ENGINE=InnoDB;'], workspace) as prepared:
            prepared.validate()
            prepared.publish()
            raise RuntimeError('SYNTHETIC failure after switch')
    assert maintenance_status(settings)['active']
    with pytest.raises(MaintenanceActiveError):
        with MySQLConnection(settings).connect():
            pytest.fail('Unresolved switch allowed business writes')


def test_consultation_records_actor_captured_before_background_work(journal, monkeypatch):
    from sme_gend.services.consultation_audit import capture_consultation, append_consultation
    settings, _ = journal
    settings = replace(settings, mysql_view_mois='2026-09')
    monkeypatch.setattr('sme_gend.services.auth_context.capture_audit_identity', lambda: 'ORIGINAL_USER')
    event = capture_consultation(settings, action='search_results_consulted', section='Recherche globale', result_count=3)
    monkeypatch.setattr('sme_gend.services.auth_context.capture_audit_identity', lambda: 'NEXT_USER')
    append_consultation(settings, event)
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT actor,sme_mois_context,JSON_EXTRACT(payload,'$.result_count') FROM audit_journal")
            assert cursor.fetchone() == ('ORIGINAL_USER', '2026-09', '3')


def test_diploma_removal_keeps_original_event_in_audit(isolated_mysql, monkeypatch):
    import json
    from test_restore_mysql import _lot7_dossier
    settings, connect, params, service, row = _lot7_dossier(isolated_mysql, monkeypatch)
    service.add_diplome(row, categorie='CIVIL', libelle='SYNTHETIC DIPLOMA')
    original = service.list_diplomes(row)[0]
    service.delete_diplome(row, original.id, expected=original)
    assert service.list_diplomes(row) == []
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT payload FROM audit_journal WHERE action='dossier_diplome_delete' ORDER BY id DESC LIMIT 1")
            payload = json.loads(cursor.fetchone()[0])
            assert payload['before']['libelle'] == 'SYNTHETIC DIPLOMA'
            assert payload['before']['id'] == original.id
            assert payload['after'] is None


def test_maintenance_keeps_valid_sessions_without_renewing_or_accepting_revoked_ones(journal):
    from sme_gend.services.auth_session_registry import ensure_auth_sessions_table, heartbeat_session
    settings, _ = journal
    ensure_auth_sessions_table(settings)
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO auth_active_session(session_token,username,client_instance_id,session_rev,expires_at) VALUES('SYNTHETIC','TEST','CLIENT',2,DATE_ADD(UTC_TIMESTAMP(),INTERVAL 1 HOUR))")
    with centralized_maintenance(settings):
        # Use a different context/thread, like an actual second client.
        with ThreadPoolExecutor(max_workers=1) as workers:
            assert workers.submit(heartbeat_session, settings, session_token='SYNTHETIC', username='TEST', session_rev=2).result(timeout=10)
            assert not workers.submit(heartbeat_session, settings, session_token='SYNTHETIC', username='TEST', session_rev=1).result(timeout=10)


def test_preexisting_transaction_is_never_committed_by_barrier_setup(isolated_mysql):
    from sme_gend.services.database_maintenance import coordinated_write
    settings, connect, params = isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE sample(id INT) ENGINE=InnoDB')
        with coordinated_write(settings, connection):
            pass
        with connection.cursor() as cursor:
            cursor.execute('SET autocommit=0')
            cursor.execute('INSERT INTO sample VALUES(1)')
        with pytest.raises(RuntimeError, match='préexistante'):
            with coordinated_write(settings, connection):
                pytest.fail('Preexisting transaction was admitted')
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM sample')
            assert cursor.fetchone() == (1,)
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM sample')
            assert cursor.fetchone() == (0,)


def test_zip_restore_preserves_events_recorded_after_backup(journal, tmp_path, monkeypatch):
    import json
    from sme_gend.services import backup_restore_service as backup
    settings, insert = journal
    insert(action='BEFORE_BACKUP', module='system')
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE sample(id INT PRIMARY KEY) ENGINE=InnoDB')
            cursor.execute('INSERT INTO sample VALUES(7)')
    path = tmp_path / 'synthetic_backup.zip'
    scope = backup.BackupScope(include_user_config=False, include_app_data=False,
        include_app_config=False, include_media_photos=False, include_media_grades=False)
    backup.create_backup(settings, path, scope=scope)
    insert(action='AFTER_BACKUP', module='consultation')
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('INSERT INTO sample VALUES(8)')
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: path)
    restore_scope = backup.RestoreScope(restore_user_config=False, restore_app_data=False,
        restore_app_config=False, restore_media_photos=False, restore_media_grades=False,
        maintenance_confirmed=True)
    backup.restore_backup(settings, path, scope=restore_scope)
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM sample ORDER BY id')
            assert cursor.fetchall() == ((7,),)
            cursor.execute('SELECT action,payload FROM audit_journal ORDER BY id')
            rows = cursor.fetchall()
            assert {row[0] for row in rows} >= {'BEFORE_BACKUP', 'AFTER_BACKUP', 'database_restored'}
            restored = next(json.loads(row[1]) for row in rows if row[0] == 'database_restored')
            assert restored['audit_preserved'] is True
    assert not maintenance_status(settings)['active']
