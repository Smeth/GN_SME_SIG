"""Partial SQL restores retain current protocol state, including absent keys."""
import json

import pytest
from test_restore_mysql import isolated_mysql

from sme_gend.services import backup_restore_service as backup


@pytest.mark.parametrize('sql_only', [False, True])
def test_partial_restore_preserves_live_operational_values_and_absence(isolated_mysql, tmp_path, monkeypatch, sql_only):
    settings, connect, params = isolated_mysql
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE ref_grade(id INT PRIMARY KEY,label TEXT) ENGINE=InnoDB')
            cursor.execute("INSERT INTO ref_grade VALUES(1,'ARCHIVED')")
            cursor.execute('CREATE TABLE app_config(config_key VARCHAR(64) PRIMARY KEY,payload JSON NOT NULL,'
                'updated_by VARCHAR(128),updated_at DATETIME(3)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci')
            for key in ('SME_DOCUMENT_CYCLE', 'actıve_month_revision', 'SHARED_PERSONNEL_MEDIA'):
                cursor.execute('INSERT INTO app_config VALUES(%s,%s,%s,%s)',
                    (key,json.dumps({'archived': True}),'ARCHIVE','2000-01-01 00:00:00.000'))
            cursor.execute("INSERT INTO app_config VALUES('ordinary',%s,'ARCHIVE','2000-01-01')", (json.dumps({'point': 'ARCHIVE'}),))
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'restore')
    archive = tmp_path/'partial.zip'
    selected = ('app_config',) if sql_only else ('ref_grade',)
    backup.create_backup(settings, archive, scope=backup.BackupScope(mysql_tables=selected,
        include_user_config=False,include_app_data=False,include_media_photos=False,
        include_media_grades=False,include_publications=False,include_app_config=not sql_only))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM app_config WHERE config_key IN('sme_document_cycle','active_month_revision','shared_personnel_media')")
            cursor.execute('INSERT INTO app_config VALUES(%s,%s,%s,%s)',
                ('sMe_DOCUMENT_CYCLE', json.dumps({'mode': 'legacy'}), 'LIVE AUTHOR', '2026-11-08 08:09:10.123'))
            cursor.execute('INSERT INTO app_config VALUES(%s,%s,%s,%s)',
                ('shared_personnel_media', json.dumps({'path': 'CURRENT'}), 'LIVE MEDIA', '2026-11-08 09:10:11.234'))
            cursor.execute("UPDATE ref_grade SET label='LIVE'")
            cursor.execute("UPDATE app_config SET payload=%s WHERE config_key='ordinary'", (json.dumps({'point': 'LIVE'}),))
            cursor.execute("SELECT config_key,payload,updated_by,updated_at FROM app_config WHERE config_key IN('sme_document_cycle','shared_personnel_media') ORDER BY config_key")
            before = cursor.fetchall()
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: archive)
    backup.restore_backup(settings, archive, scope=backup.RestoreScope(mysql_tables=selected,
        restore_user_config=False,restore_app_data=False,restore_media_photos=False,restore_media_grades=False,
        restore_publications=False,restore_app_config=not sql_only,preserve_local_accounts=False,maintenance_confirmed=True))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT config_key,payload,updated_by,updated_at FROM app_config WHERE config_key IN('sme_document_cycle','shared_personnel_media') ORDER BY config_key")
            assert cursor.fetchall() == before
            cursor.execute("SELECT COUNT(*) FROM app_config WHERE config_key='active_month_revision'")
            assert cursor.fetchone()[0] == 0
            cursor.execute("SELECT payload FROM app_config WHERE config_key='ordinary'")
            assert json.loads(cursor.fetchone()[0]) == {'point': 'ARCHIVE'}
            cursor.execute('SELECT label FROM ref_grade')
            assert cursor.fetchone()[0] == ('LIVE' if sql_only else 'ARCHIVED')


def test_legacy_full_archive_is_refused_when_it_omits_installed_extension_facts(isolated_mysql, tmp_path, monkeypatch):
    from test_publication_registry_mysql import migrate, revision
    from sme_gend.services import sme_publication_registry as registry
    from sme_gend.services.configuration_restore import IncompatibleRestoreError
    from sme_gend.services.database_maintenance import maintenance_status
    settings, connect, params, old_archive = migrate(isolated_mysql, tmp_path, monkeypatch)
    publication = registry.register_export(settings, revision(settings, tmp_path/'document'))
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'restore')
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: old_archive)
    with pytest.raises(IncompatibleRestoreError, match='extensions installées'):
        backup.restore_full_backup(settings, old_archive, restore_user_config=False,
            restore_app_data=False,restore_personnel_media=False,restore_publications=False,
            preserve_local_accounts=False,maintenance_confirmed=True)
    assert not maintenance_status(settings)['active']
    assert registry.get_publication(settings, publication.id).id == publication.id
    assert registry.verify_publication(settings, publication.id)['verified']


@pytest.mark.parametrize('selected', [('agent_effectif_movement',), ('carriere_situation',)])
def test_partial_sql_restore_refuses_incomplete_membership_cohort(isolated_mysql, tmp_path, monkeypatch, selected):
    from test_publication_registry_mysql import migrate
    from sme_gend.services.database_maintenance import maintenance_status
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'restore')
    archive = tmp_path/'partial-membership.zip'
    backup.create_backup(settings, archive, scope=backup.BackupScope(mysql_tables=selected,
        include_user_config=False,include_app_data=False,include_media_photos=False,
        include_media_grades=False,include_publications=False,include_app_config=False))
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: archive)
    with pytest.raises(ValueError, match='mouvements datés'):
        backup.restore_backup(settings, archive, scope=backup.RestoreScope(mysql_tables=selected,
            restore_user_config=False,restore_app_data=False,restore_media_photos=False,restore_media_grades=False,
            restore_publications=False,restore_app_config=False,preserve_local_accounts=False,maintenance_confirmed=True))
    assert not maintenance_status(settings)['active']
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT noms FROM agent WHERE id=1')
            assert cursor.fetchone()[0] == 'NOM INITIAL'


def test_restored_membership_must_match_its_dated_sources_before_switch(isolated_mysql, tmp_path, monkeypatch):
    from test_publication_registry_mysql import migrate
    from sme_gend.services.configuration_restore import IncompatibleRestoreError
    from sme_gend.services.database_maintenance import maintenance_status
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    selected = ('agent_effectif_movement', 'sme_month_membership')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO agent_effectif_movement(id,agent_id,category,movement_kind,effective_date,recorded_at,recorded_by) "
                "VALUES(1,1,'PERTE','SYNTHETIC','2026-11-01',UTC_TIMESTAMP(6),'TEST')")
            cursor.execute('INSERT INTO sme_month_membership(agent_id,sme_periode_id,is_member,baseline_member,event_id,updated_at) '
                'VALUES(1,1,1,1,1,UTC_TIMESTAMP(6))')  # FK/CHECK valid, semantics wrong.
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'restore')
    archive = tmp_path/'invalid-membership.zip'
    backup.create_backup(settings, archive, scope=backup.BackupScope(mysql_tables=selected,
        include_user_config=False,include_app_data=False,include_media_photos=False,
        include_media_grades=False,include_publications=False,include_app_config=False))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('UPDATE sme_month_membership SET is_member=0')
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: archive)
    with pytest.raises(IncompatibleRestoreError, match='aucune bascule') as error:
        backup.restore_backup(settings, archive, scope=backup.RestoreScope(mysql_tables=selected,
            restore_user_config=False,restore_app_data=False,restore_media_photos=False,restore_media_grades=False,
            restore_publications=False,restore_app_config=False,preserve_local_accounts=False,maintenance_confirmed=True))
    assert 'Projection d’effectif incohérente' in str(error.value.__cause__)
    assert not maintenance_status(settings)['active']
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT is_member FROM sme_month_membership')
            assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize('selected', [('_sme_schema_migration',), ('sme_publication_write_gate',),
                                    ('sme_temporal_change',), ('sme_projection_dirty',)])
def test_partial_restore_cannot_roll_back_protocol_metadata_only(isolated_mysql, tmp_path, monkeypatch, selected):
    from test_publication_registry_mysql import migrate
    from sme_gend.services import schema_migrations
    from sme_gend.services.configuration_restore import IncompatibleRestoreError
    from sme_gend.services.database_maintenance import maintenance_status
    settings, connect, params, _ = migrate(isolated_mysql, tmp_path, monkeypatch)
    monkeypatch.setattr(backup, 'default_backup_directory', lambda: tmp_path/'restore')
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM _sme_schema_migration WHERE version='0003'")
            current = cursor.fetchone()
            cursor.execute("DELETE FROM _sme_schema_migration WHERE version='0003'")
    archive = tmp_path/'old-protocol-only.zip'
    try:
        backup.create_backup(settings, archive, scope=backup.BackupScope(mysql_tables=selected,
            include_user_config=False,include_app_data=False,include_media_photos=False,
            include_media_grades=False,include_publications=False,include_app_config=False))
    finally:
        with connect(**params) as connection:
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO _sme_schema_migration VALUES(' + ','.join(['%s']*len(current)) + ')', current)
    monkeypatch.setattr(backup, 'ensure_recent_backup', lambda *a, **kw: archive)
    with pytest.raises(IncompatibleRestoreError, match='pas être restaurés séparément'):
        backup.restore_backup(settings, archive, scope=backup.RestoreScope(mysql_tables=selected,
            restore_user_config=False,restore_app_data=False,restore_media_photos=False,restore_media_grades=False,
            restore_publications=False,restore_app_config=False,preserve_local_accounts=False,maintenance_confirmed=True))
    assert not maintenance_status(settings)['active']
    assert all(row['state'] == 'applied' for row in schema_migrations.schema_migration_status(settings))
    with connect(**params) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM sme_publication_write_gate')
            assert cursor.fetchall() == ((1,),)
