"""Shared revisions/approvals on the explicitly isolated MySQL test instance."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from threading import Barrier

import pytest
from openpyxl import Workbook
from test_restore_mysql import isolated_mysql
from test_sme_export_snapshot_mysql import seed

from sme_gend.services import sme_publication_registry as registry
from sme_gend.services import schema_migrations as migrations
from sme_gend.services.sme_export_revision import save_export_revision


def migrate(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params = seed(isolated_mysql)
    archive = install_registry(settings, tmp_path, monkeypatch)
    return settings, connect, params, archive


def install_registry(settings, tmp_path, monkeypatch):
    from sme_gend.services import backup_restore_service as backup
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    archive = tmp_path / "before.zip"
    backup.create_full_backup(settings, archive)
    result = migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
        safety_backup=archive, maintenance_confirmed=True)
    assert result["applied"] == ["0001", "0002", "0003"]
    return archive


def revision(settings, root, *, payload=b"WORKBOOK TEST"):
    from sme_gend.services.numerique_excel_export import _capture_numerique_workbook_data
    model = Workbook()
    model.active.title = "NUMERIQUE"
    model.active["B7"] = "CNE"
    stream = BytesIO()
    model.save(stream)
    model.close()
    dataset, source = _capture_numerique_workbook_data(settings, "2026-11", ["CNE"], settings)
    def render(template, destination):
        destination.write_bytes(payload)
        return ("NUMERIQUE",)
    return save_export_revision(root=root, mois="2026-11", template_bytes=stream.getvalue(),
        dataset=dataset, source=source, actor="TEST", renderer=render)


def test_capability_check_never_creates_schema_and_explicit_apply_is_idempotent(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params = seed(isolated_mysql)
    with pytest.raises(migrations.SchemaMigrationRequired):
        migrations.require_capability(settings, "publication_registry")
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'sme_publication%'")
            assert not cursor.fetchall()
    # Separate fixture initialization is already seeded, so build its backup here.
    from sme_gend.services import backup_restore_service as backup
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    archive = tmp_path / "before.zip"
    backup.create_full_backup(settings, archive)
    for _ in range(2):
        result = migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
            safety_backup=archive, maintenance_confirmed=True)
        assert result["applied"] == (["0001", "0002", "0003"] if _ == 0 else [])
    assert all(row["state"] == "applied" for row in migrations.schema_migration_status(settings))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            for table, index, expected in migrations.TEMPORAL_INDEXES:
                cursor.execute("SELECT column_name FROM information_schema.statistics WHERE table_schema=DATABASE() "
                               "AND table_name=%s AND index_name=%s ORDER BY seq_in_index", (table, index))
                assert tuple(row[0] for row in cursor.fetchall()) == expected


def test_central_chunks_can_be_downloaded_from_another_client_without_local_files(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    payload = b"X" * (registry.CHUNK_SIZE + 31)
    local = revision(settings, tmp_path / "versions", payload=payload)
    publication = registry.register_export(settings, local)
    assert (publication.status, publication.revision_no, publication.version) == ("brouillon", 1, 1)
    repeated = registry.import_legacy_revision(settings, local.folder)
    assert repeated.id == publication.id
    for file in local.folder.iterdir():
        file.unlink()
    local.folder.rmdir()
    client = replace(settings, mysql_view_mois="2026-01")
    destination = tmp_path / "other-client.xlsx"
    registry.download_publication(client, publication.id, destination)
    assert destination.read_bytes() == payload
    assert registry.verify_publication(client, publication.id)["verified"]
    assert registry.validate_registry_snapshot(settings) == {"publications": 1, "verified": True}


def test_numbering_is_central_and_concurrent_registration_is_atomic(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    locals_ = [revision(settings, tmp_path / str(index)) for index in range(2)]
    barrier = Barrier(2)
    def work(item):
        barrier.wait(timeout=10)
        return registry.register_export(settings, item)
    with ThreadPoolExecutor(max_workers=2) as executor:
        publications = list(executor.map(work, locals_))
    assert sorted(item.revision_no for item in publications) == [1, 2]
    assert len({item.id for item in publications}) == 2
    assert len(registry.list_publications(settings, mois="2026-11")) == 2
    same = revision(settings, tmp_path / "same")
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        repeated = list(executor.map(work, [same, same]))
    assert repeated[0].id == repeated[1].id
    assert repeated[0].revision_no == repeated[1].revision_no == 3
    assert len(registry.list_publications(settings, mois="2026-11")) == 3


def test_validation_replacement_audit_and_stale_data_are_consistent(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    first = registry.register_export(settings, revision(settings, tmp_path / "first"))
    approved = registry.validate_publication(settings, first.id, expected_version=first.version)
    assert approved.status == "valide" and approved.version == 2
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE agent SET noms='NOM CORRIGE' WHERE id=1")
    second = registry.register_export(settings, revision(settings, tmp_path / "second"))
    with pytest.raises(registry.PublicationConflict):
        registry.validate_publication(settings, second.id, expected_version=second.version)
    with pytest.raises(ValueError, match="motif"):
        registry.replace_publication(settings, second.id, expected_version=second.version,
                                     expected_current_id=first.id, reason=" ")
    newer = registry.replace_publication(settings, second.id, expected_version=second.version,
                                        expected_current_id=first.id, reason="Correction du nom après contrôle")
    old = registry.get_publication(settings, first.id)
    assert old.status == "remplace" and old.replaced_by_id == newer.id
    assert registry.require_validated_publication(settings, "2026-11").id == newer.id
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT action FROM audit_journal WHERE module='sme_publication' ORDER BY id")
            actions = [row[0] for row in cursor.fetchall()]
    assert actions.count("publication_created") == 2
    assert actions.count("publication_validated") == actions.count("publication_replaced") == 1
    stale = registry.register_export(settings, revision(settings, tmp_path / "stale"))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE agent SET noms='MODIFICATION ULTERIEURE' WHERE id=1")
    with pytest.raises(registry.PublicationDataChanged):
        registry.replace_publication(settings, stale.id, expected_version=stale.version,
                                     expected_current_id=newer.id, reason="Tentative sur un document ancien")
    assert registry.get_publication(settings, newer.id).status == "valide"
    assert registry.get_publication(settings, stale.id).status == "brouillon"


def test_audit_failure_rolls_back_number_and_all_chunks(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    local = revision(settings, tmp_path / "local")
    monkeypatch.setattr(registry, "_audit", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("audit failure")))
    with pytest.raises(RuntimeError, match="audit failure"):
        registry.register_export(settings, local)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            for name in ("sme_publication", "sme_publication_artifact", "sme_publication_chunk"):
                cursor.execute("SELECT COUNT(*) FROM " + name)
                assert cursor.fetchone()[0] == 0


def test_tampered_chunk_cannot_replace_an_existing_download(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    publication = registry.register_export(settings, revision(settings, tmp_path / "local"))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sme_publication_chunk SET content='TAMPERED' WHERE publication_id=%s AND artifact_name='classeur.xlsx'", (publication.id,))
    dest = tmp_path / "keep.xlsx"
    dest.write_bytes(b"PREVIOUS VALID WORKBOOK")
    with pytest.raises(ValueError, match="altérée"):
        registry.download_publication(settings, publication.id, dest)
    assert dest.read_bytes() == b"PREVIOUS VALID WORKBOOK"


def test_simultaneous_approvals_leave_exactly_one_current_revision(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    candidates = [registry.register_export(settings, revision(settings, tmp_path / str(index))) for index in range(2)]
    barrier = Barrier(2)
    def approve(publication):
        barrier.wait(timeout=10)
        try:
            return registry.validate_publication(settings, publication.id, expected_version=publication.version)
        except registry.PublicationConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(approve, candidates))
    assert len([result for result in results if result is not None]) == 1
    assert len(registry.list_publications(settings, status="valide")) == 1
    assert registry.validate_registry_snapshot(settings)["verified"]


def test_change_in_another_month_does_not_invalidate_the_logical_document(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services.data_sync import publish_data_change
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    candidate = registry.register_export(settings, revision(settings, tmp_path / "candidate"))
    publish_data_change(settings, reason="OTHER MONTH", mois="2026-12")
    validated = registry.validate_publication(settings, candidate.id, expected_version=candidate.version)
    assert validated.status == "valide"


def test_committed_mirror_error_reports_saved_publication_instead_of_false_failure(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    original = registry._audit
    def audit_and_fail_mirror(*args, **kwargs):
        original(*args, **kwargs)
        after_mysql_commit(lambda: (_ for _ in ()).throw(OSError("synthetic mirror failure")))
    monkeypatch.setattr(registry, "_audit", audit_and_fail_mirror)
    created = registry.register_export(settings, revision(settings, tmp_path / "created"))
    assert created.operation_warning and created.status == "brouillon"
    validated = registry.validate_publication(settings, created.id, expected_version=created.version)
    assert validated.operation_warning and validated.status == "valide"
    assert registry.get_publication(settings, validated.id).status == "valide"


def test_migration_refuses_backup_from_another_server_before_any_new_table(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services import backup_restore_service as backup
    settings, connect, params = seed(isolated_mysql)
    marker = tmp_path / "fake.zip"
    marker.write_bytes(b"only mocked manifest is read")
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            tables = {row[0]: 0 for row in cursor.fetchall()}
    manifest = backup.BackupManifest(4, "2026-11-08", settings.mysql_database, settings.mysql_host, tables, [],
        snapshot_consistency={"sql_and_shared_config": "same_transaction", "server_uuid": "ANOTHER SERVER", "database_name": settings.mysql_database},
        file_inventory={"mysql_dump.sql": {"sha256": "0" * 64, "size": 0}})
    monkeypatch.setattr(backup, "inspect_backup", lambda path: manifest)
    with pytest.raises(ValueError, match="autre serveur"):
        migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
            safety_backup=marker, maintenance_confirmed=True)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES LIKE 'sme_publication%'")
            assert not cursor.fetchall()


def test_full_sql_backup_restores_shared_documents_and_approval_state(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services import backup_restore_service as backup
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    monkeypatch.setattr(backup, "default_backup_directory", lambda: tmp_path / "backups")
    content = b"OFFICIAL WORKBOOK" + b"X" * (registry.CHUNK_SIZE + 17)
    created = registry.register_export(settings, revision(settings, tmp_path / "local", payload=content))
    approved = registry.validate_publication(settings, created.id, expected_version=created.version)
    archive = tmp_path / "published.zip"
    manifest = backup.create_full_backup(settings, archive)
    assert set(registry.registry_tables()).issubset(manifest.table_counts)
    # Simulate losing the document rows; all operations remain in the test schema.
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            for table in ("sme_publication_chunk", "sme_publication_artifact", "sme_publication", "sme_publication_period"):
                cursor.execute("DELETE FROM " + table)
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    safety = tmp_path / "safety.zip"
    backup.create_full_backup(settings, safety)
    monkeypatch.setattr(backup, "ensure_recent_backup", lambda *a, **kw: safety)
    backup.restore_full_backup(settings, archive, restore_user_config=False, restore_app_data=False,
        restore_personnel_media=False, restore_publications=False, preserve_audit_journal=False,
        preserve_local_accounts=False, maintenance_confirmed=True)
    restored = registry.require_validated_publication(settings, "2026-11")
    assert restored.id == approved.id and restored.revision_no == approved.revision_no
    destination = tmp_path / "recovered.xlsx"
    registry.download_publication(settings, restored.id, destination)
    assert destination.read_bytes() == content
    assert registry.validate_registry_snapshot(settings)["verified"]


def test_malformed_existing_table_is_not_silently_adopted(isolated_mysql, tmp_path, monkeypatch):
    from sme_gend.services import backup_restore_service as backup
    settings, connect, params = seed(isolated_mysql)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE sme_publication_period(mois VARCHAR(20), next_revision INT) ENGINE=InnoDB")
            cursor.execute("INSERT INTO sme_publication_period VALUES('EXISTING DATA',777)")
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    archive = tmp_path / "before.zip"
    backup.create_full_backup(settings, archive)
    with pytest.raises(ValueError, match="préexistante.*incompatible"):
        migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
            safety_backup=archive, maintenance_confirmed=True)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM sme_publication_period")
            assert cursor.fetchall() == (("EXISTING DATA", 777),)
            cursor.execute("SHOW TABLES LIKE '_sme_schema_migration'")
            assert not cursor.fetchall()


def test_runtime_capability_refuses_schema_changed_after_installation(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE sme_publication DROP INDEX uk_publication_month_revision")
    with pytest.raises(migrations.SchemaMigrationRequired):
        migrations.require_capability(settings, "publication_registry")
    status = next(row for row in migrations.schema_migration_status(settings) if row["capability"] == "publication_registry")
    assert status["state"] == "incomplete" and status["contract_errors"]


def test_restore_validator_rejects_replacement_link_to_an_unapproved_draft(isolated_mysql, tmp_path, monkeypatch):
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    first = registry.register_export(settings, revision(settings, tmp_path / "first"))
    second = registry.register_export(settings, revision(settings, tmp_path / "second"))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE sme_publication SET status='remplace',validated_at=UTC_TIMESTAMP(6),"
                           "validated_by='FORGED',replaced_by_id=%s WHERE id=%s", (second.id, first.id))
    with pytest.raises(ValueError, match="remplacement"):
        registry.validate_registry_snapshot(settings)


def test_interrupted_additive_migration_records_failure_and_requires_explicit_resume(isolated_mysql, tmp_path, monkeypatch):
    import pymysql
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.services.database_maintenance import maintenance_status
    settings, connect, params = seed(isolated_mysql)
    monkeypatch.setattr(backup, "_collect_config_paths", lambda: [])
    monkeypatch.setattr(backup, "_iter_personnel_media_files", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "project_root", lambda: tmp_path / "app")
    archive = tmp_path / "before.zip"
    backup.create_full_backup(settings, archive)
    original = pymysql.cursors.Cursor.execute
    failed = []
    def transient(cursor, statement, args=None):
        if not failed and str(statement).lstrip().startswith("CREATE TABLE IF NOT EXISTS sme_month_membership"):
            failed.append(True)
            raise RuntimeError("synthetic DDL interruption")
        return original(cursor, statement, args)
    monkeypatch.setattr(pymysql.cursors.Cursor, "execute", transient)
    with pytest.raises(RuntimeError, match="reprise explicite"):
        migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
            safety_backup=archive, maintenance_confirmed=True)
    assert not maintenance_status(settings)["active"]
    status = {row["version"]: row["state"] for row in migrations.schema_migration_status(settings)}
    assert status == {"0001": "applied", "0002": "applied", "0003": "failed"}
    with pytest.raises(ValueError, match="reprise explicite"):
        migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
            safety_backup=archive, maintenance_confirmed=True)
    after_failure = tmp_path / "partial-state.zip"
    backup.create_full_backup(settings, after_failure)
    result = migrations.apply_schema_migrations(settings, expected_database=settings.mysql_database,
        safety_backup=after_failure, maintenance_confirmed=True, allow_resume=True)
    assert result["applied"] == ["0003"]
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT noms FROM agent WHERE id=1")
            assert cursor.fetchone()[0] == "NOM INITIAL"
