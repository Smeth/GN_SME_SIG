from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
from dataclasses import replace
from types import SimpleNamespace
import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier
from test_sensitive_audit_mysql import journal
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.database_maintenance import centralized_maintenance, maintenance_status, MaintenanceActiveError


def test_two_clients_can_update_different_agents_without_waiting_for_each_other(isolated_mysql):
    settings, connect, params = isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE sample_agent(id INT PRIMARY KEY, position_admin VARCHAR(40)) ENGINE=InnoDB')
            cursor.execute("INSERT INTO sample_agent VALUES (1,'Présent'),(2,'Présent')")
    entered = [Event(), Event()]
    release = Event()
    def edit(index):
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE sample_agent SET position_admin=%s WHERE id=%s', ('Mission' if index == 0 else 'Congé', index+1))
            entered[index].set()
            assert release.wait(10)
    started = monotonic()
    with ThreadPoolExecutor(max_workers=2) as workers:
        jobs = [workers.submit(edit, index) for index in range(2)]
        try:
            assert all(event.wait(3) for event in entered), 'A client serialized the other client behind a global lock'
            assert not maintenance_status(settings)['active']
            print('parallel_write_admission_seconds=', round(monotonic()-started, 3))
        finally:
            release.set()
        for job in jobs:
            job.result(timeout=5)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT position_admin FROM sample_agent ORDER BY id')
            assert cursor.fetchall() == (('Mission',), ('Congé',))


def test_search_and_reference_reads_remain_available_while_another_client_edits(isolated_mysql, monkeypatch):
    from sme_gend.services.personnel_lookup_service import PersonnelLookupService
    from sme_gend.repositories.grade_repository import GradeRepository
    from sme_gend.repositories.structure_repository import StructureRepository
    settings, _, _, _, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois='2026-09')
    # An already connected client has completed its one-shot config migration.
    from sme_gend.services import app_config_store
    from sme_gend.repositories.mysql_connection import mysql_database_key
    monkeypatch.setattr(app_config_store, '_ensure_config_done', {mysql_database_key(settings)})
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: settings)
    monkeypatch.setattr('sme_gend.services.personnel_lookup_service.load_sme_context', lambda: SimpleNamespace(mois_actif='2026-09'))
    entered, release = Event(), Event()
    def editor():
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE carriere_situation SET position_admin_libelle='Mission' WHERE id=1")
            entered.set()
            assert release.wait(10)
    with ThreadPoolExecutor(max_workers=2) as workers:
        editing = workers.submit(editor)
        assert entered.wait(5)
        def search():
            from sme_gend.services import database_maintenance
            def forbidden(*a, **kw):
                pytest.fail('A search or reference read entered write coordination')
            with monkeypatch.context() as context:
                context.setattr(database_maintenance, 'coordinated_write', forbidden)
                hits = PersonnelLookupService(settings).search_extended('SYNTHETIC')
                GradeRepository(settings).fetch_all()
                StructureRepository(settings).fetch_all()
                return hits
        try:
            hits = workers.submit(search).result(timeout=3)
            assert hits
            assert not release.is_set()
        finally:
            release.set()
        editing.result(timeout=5)


def test_real_maintenance_waits_for_active_writer_then_blocks_new_writes(journal):
    settings, _ = journal
    writing, finish_write, restoring, finish_restore = Event(), Event(), Event(), Event()
    def writer():
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO audit_journal(ts_utc,action,module) VALUES(UTC_TIMESTAMP(),'SYNTHETIC_WRITE','system')")
            writing.set()
            assert finish_write.wait(12)
    def restore():
        with centralized_maintenance(settings):
            restoring.set()
            assert finish_restore.wait(12)
    with ThreadPoolExecutor(max_workers=2) as workers:
        write_job = workers.submit(writer)
        assert writing.wait(5)
        restore_job = workers.submit(restore)
        try:
            deadline = monotonic()+3
            while not maintenance_status(settings)['active'] and monotonic() < deadline:
                sleep(0.01)
            assert maintenance_status(settings)['active']
            assert not restoring.is_set(), 'Maintenance started before the active writer committed'
            with pytest.raises(MaintenanceActiveError):
                with MySQLConnection(settings).connect():
                    pytest.fail('New writer entered actual maintenance')
            finish_write.set()
            write_job.result(timeout=5)
            assert restoring.wait(5)
        finally:
            finish_write.set()
            finish_restore.set()
        restore_job.result(timeout=5)
    assert not maintenance_status(settings)['active']


def test_disconnected_writer_does_not_leave_a_fake_maintenance(journal):
    from sme_gend.services.database_maintenance import WRITERS_TABLE
    settings, _ = journal
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO `{WRITERS_TABLE}`(token,lock_name,connection_id) VALUES(%s,%s,%s)", ('f'*32,'SYNTHETIC_ABANDONED_LOCK',99999999))
    with centralized_maintenance(settings):
        assert maintenance_status(settings)['active']
    assert not maintenance_status(settings)['active']
