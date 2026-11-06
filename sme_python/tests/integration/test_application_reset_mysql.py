from pathlib import Path
import json
import pytest
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services
from sme_gend.services.application_reset import reset_application, reset_target, ResetOptions


def prepare(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.config.auth_config import AuthConfig
    settings, connect, params, _, _ = setup_services(isolated_mysql, monkeypatch)
    monkeypatch.setattr('sme_gend.config.get_settings',lambda:settings)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor',lambda:'test_admin')
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda *a: True)
    monkeypatch.setattr('sme_gend.services.app_config_store.ensure_central_config_ready', lambda *a: None)
    monkeypatch.setattr(backup, '_collect_config_paths', lambda: [])
    monkeypatch.setattr(backup, '_collect_personnel_media_roots', lambda s: [])
    monkeypatch.setattr(backup, 'create_backup_to_default_dir', lambda s: make_backup(s, backup, tmp_path))
    local = tmp_path / 'monthly.mdb'
    local.write_bytes(b'OLD MONTH')
    monkeypatch.setattr('sme_gend.services.application_reset_files.reset_local_paths', lambda *a, **kw: [local])
    auth = AuthConfig.from_dict({'enabled': True}).to_dict()
    auth['users'] = [{'username':'existing', 'salt':'salt', 'password_hash':'hash', 'role':'super_admin'}]
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO app_config(config_key,payload) VALUES('auth_config',%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload)", (json.dumps(auth),))
            cur.execute("INSERT INTO auth_active_session(session_token,username,client_instance_id,expires_at) VALUES('old-token','existing','old-client',DATE_ADD(UTC_TIMESTAMP(),INTERVAL 1 DAY))")
            cur.execute("INSERT INTO audit_journal(ts_utc,action,module,payload) VALUES(UTC_TIMESTAMP(),'doc_gen','doc_gen','{}'),(UTC_TIMESTAMP(),'old','personnel','{}')")
    return settings, connect, params, auth, local


def make_backup(settings, backup, tmp_path):
    path = tmp_path / 'before_reset.zip'
    backup.create_full_backup(settings, path)
    return path


@pytest.mark.parametrize('keep_users',[True,False])
@pytest.mark.parametrize('keep_documents',[True,False])
def test_reset_choices_are_independent_and_saved(isolated_mysql, monkeypatch, tmp_path, keep_users, keep_documents):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_service import verify_password
    from sme_gend.config.auth_config import AuthUser
    settings, connect, params, old_auth, local = prepare(isolated_mysql, monkeypatch, tmp_path)
    result = reset_application(settings, ResetOptions(keep_users,keep_documents), confirmation=reset_target(settings),
                               new_username='new_admin', new_password='Test-Reset-Secret-123')
    assert Path(result['backup']).is_file() and not local.exists()
    assert Path(result['recovery'],'0.original').read_bytes()==b'OLD MONTH'
    repo = AppConfigRepository(settings)
    current = repo.get_json('auth_config')
    if keep_users:
        assert current==old_auth
    else:
        assert [u['username'] for u in current['users']]==['new_admin']
        assert current['enabled']
        assert verify_password('Test-Reset-Secret-123',AuthUser.from_dict(current['users'][0]))
    assert repo.get_json('migration_v2_complete')=={'reset':True}
    assert repo.get_json('sme_workspace')=={}
    with connect(**params) as conn:
        with conn.cursor() as cur:
            for table in ('agent','carriere_situation','sme_periode','auth_active_session'):
                cur.execute(f'SELECT COUNT(*) FROM {table}')
                assert cur.fetchone()[0]==0
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE module='doc_gen'")
            assert cur.fetchone()[0]==int(keep_documents)
            cur.execute("SELECT COUNT(*) FROM audit_journal WHERE action='application_reset'")
            assert cur.fetchone()[0]==1
    # The safety archive really contains the agents and original users.
    import zipfile
    with zipfile.ZipFile(result['backup']) as z:
        sql=z.read('mysql_dump.sql').decode()
        assert 'INSERT INTO `agent`' in sql and 'existing'.encode().hex() in sql


def test_reset_failure_rolls_back_database_and_local_files(isolated_mysql, monkeypatch, tmp_path):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    settings, connect, params, old_auth, local = prepare(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr(AppConfigRepository,'append_audit',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('AUDIT FAILURE')))
    with pytest.raises(RuntimeError,match='AUDIT FAILURE'):
        reset_application(settings,ResetOptions(False,False),confirmation=reset_target(settings),new_username='new',new_password='Test-Reset-Secret-123')
    assert local.read_bytes()==b'OLD MONTH'
    assert AppConfigRepository(settings).get_json('auth_config')==old_auth
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM agent')
            assert cur.fetchone()[0]>0


def test_reset_failed_backup_never_deletes(isolated_mysql, monkeypatch, tmp_path):
    settings, connect, params, old_auth, local = prepare(isolated_mysql, monkeypatch, tmp_path)
    monkeypatch.setattr('sme_gend.services.backup_restore_service.create_backup_to_default_dir',lambda *a: (_ for _ in ()).throw(RuntimeError('BACKUP FAILED')))
    with pytest.raises(RuntimeError,match='BACKUP FAILED'):
        reset_application(settings,ResetOptions(),confirmation=reset_target(settings))
    assert local.exists()
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM agent')
            assert cur.fetchone()[0]>0
