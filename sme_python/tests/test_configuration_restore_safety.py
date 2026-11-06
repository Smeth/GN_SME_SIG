"""Configuration-only restore: synthetic paths and UI results."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from zipfile import ZipFile
import pytest
from sme_gend.config.settings import Settings
from sme_gend.services import backup_restore_service as backup


def synthetic_archive(tmp_path,entries):
    path=tmp_path/'configuration.zip'
    manifest=backup.BackupManifest(4,'2026-10-30','synthetic','invalid.test',{},[],app_config_exported=True)
    with ZipFile(path,'w') as archive:
        archive.writestr(backup.MANIFEST_NAME,json.dumps(manifest.to_dict()))
        for name,value in entries.items():archive.writestr(name,value)
    return path


@pytest.mark.parametrize('local',[{}, {'personnel_media_root_path':r'\\current\share\media'}])
def test_configuration_only_restore_preserves_paths_and_their_absence(tmp_path,monkeypatch,local):
    target=tmp_path/'profile/paths.json';target.parent.mkdir();target.write_text(json.dumps(local),encoding='utf-8')
    monkeypatch.setattr(backup,'default_backup_directory',lambda:tmp_path/'backups')
    monkeypatch.setattr(backup,'user_paths_config_file',lambda:target)
    archived={'personnel_media_root_path':r'\\obsolete\share\media','sme_archives_path':r'Z:\old-archives',
              'database_path':r'Z:\old.mdb','template_excel_path':r'P:\old.xlsx','theme_preference':'archived'}
    archive=synthetic_archive(tmp_path,{'user_config/paths.json':json.dumps(archived)})
    scope=backup.RestoreScope(restore_mysql=False,restore_app_config=False,restore_app_data=False,
        restore_media_photos=False,restore_media_grades=False,preserve_local_accounts=False)
    backup.restore_backup(Settings(database_path=tmp_path/'unused'),archive,scope=scope)
    restored=json.loads(target.read_text(encoding='utf-8'))
    assert {key:restored[key] for key in archived if key!='theme_preference' and key in restored}==local
    assert restored['theme_preference']=='archived'


def test_invalid_existing_paths_refuses_restore_without_replacing_them(tmp_path,monkeypatch):
    target=tmp_path/'paths.json';target.write_bytes(b'{invalid existing config')
    with pytest.raises(ValueError):backup._merged_paths_json({'personnel_media_root_path':r'\\old\share'},target)
    assert target.read_bytes()==b'{invalid existing config'


def test_restore_ui_does_not_report_success_when_worker_returns_no_result(monkeypatch):
    from sme_gend.ui.views import backup_restore_view as ui
    fake=SimpleNamespace(_append_log=MagicMock(),window=MagicMock())
    monkeypatch.setattr(ui,'run_with_progress',lambda *args,**kwargs:None)
    info=MagicMock();monkeypatch.setattr(ui.QMessageBox,'information',info)
    ui.BackupRestoreView._finish_restore(fake,lambda:None,'synthetic.zip',False)
    info.assert_not_called()
    fake.window.assert_not_called()
    assert not any('✓' in str(call) for call in fake._append_log.call_args_list)



def test_configuration_only_restore_keeps_client_identity_and_connection(tmp_path,monkeypatch):
    target=tmp_path/'user/paths.json';target.parent.mkdir();target.write_text('{"mysql_host":"current.invalid"}',encoding='utf-8')
    identity=target.parent/'client_instance.json';identity.write_text('{"id":"CURRENT"}',encoding='utf-8')
    monkeypatch.setattr(backup,'default_backup_directory',lambda:tmp_path/'backups')
    monkeypatch.setattr(backup,'user_paths_config_file',lambda:target)
    monkeypatch.setattr(backup,'user_application_data_dir',lambda:target.parent)
    archive=synthetic_archive(tmp_path,{'user_config/paths.json':'{"mysql_host":"archived.invalid","personnel_media_root_path":"Z:/old"}',
                                      'user_config/client_instance.json':'{"id":"ARCHIVED"}'})
    scope=backup.RestoreScope(restore_mysql=False,restore_app_config=False,restore_app_data=False,
        restore_media_photos=False,restore_media_grades=False,preserve_local_accounts=False,preserve_mysql_connection=False)
    backup.restore_backup(Settings(database_path=tmp_path/'unused'),archive,scope=scope)
    assert json.loads(target.read_text())=={'mysql_host':'current.invalid'}
    assert identity.read_text()=='{"id":"CURRENT"}'



def test_concurrent_paths_edit_during_merge_is_not_overwritten(tmp_path,monkeypatch):
    target=tmp_path/'paths.json';target.write_text('{"personnel_media_root_path":"C:/before"}',encoding='utf-8')
    monkeypatch.setattr(backup,'default_backup_directory',lambda:tmp_path/'backups')
    monkeypatch.setattr(backup,'user_paths_config_file',lambda:target)
    original=backup._merged_paths_json
    def changed_during_merge(*args,**kwargs):
        merged=original(*args,**kwargs)
        target.write_text('{"personnel_media_root_path":"C:/edited-now"}',encoding='utf-8')
        return merged
    monkeypatch.setattr(backup,'_merged_paths_json',changed_during_merge)
    archive=synthetic_archive(tmp_path,{'user_config/paths.json':'{"theme":"archived"}'})
    scope=backup.RestoreScope(restore_mysql=False,restore_app_config=False,restore_app_data=False,
        restore_media_photos=False,restore_media_grades=False,preserve_local_accounts=False)
    with pytest.raises(RuntimeError):backup.restore_backup(Settings(database_path=tmp_path/'unused'),archive,scope=scope)
    assert json.loads(target.read_text())=={'personnel_media_root_path':'C:/edited-now'}



def test_restore_ui_keeps_success_flow_for_a_valid_manifest(monkeypatch):
    from sme_gend.ui.views import backup_restore_view as ui
    window=SimpleNamespace(reload_runtime_configuration=MagicMock(),refresh_for_month_change=MagicMock())
    fake=SimpleNamespace(_append_log=MagicMock(),window=lambda:window)
    result=backup.BackupManifest(4,'2026-10-30','synthetic','invalid.test',{},[])
    monkeypatch.setattr(ui,'run_with_progress',lambda *args,**kwargs:result)
    info=MagicMock();monkeypatch.setattr(ui.QMessageBox,'information',info)
    ui.BackupRestoreView._finish_restore(fake,lambda:result,'synthetic.zip',False)
    info.assert_called_once()
    window.reload_runtime_configuration.assert_called_once()


@pytest.mark.parametrize('tamper',['target','staged','content'])
def test_recovery_refuses_modified_paths_or_staged_content(tmp_path,monkeypatch,tamper):
    from io import BytesIO
    from sme_gend.services import configuration_restore as restore
    from sme_gend.services.restore_files import RestoreFiles
    user=tmp_path/'user';user.mkdir()
    target=user/'preferences.json';target.write_bytes(b'OLD')
    monkeypatch.setattr(backup,'user_application_data_dir',lambda:user)
    files=RestoreFiles(tmp_path/'private')
    files.add(target,BytesIO(b'NEW'))
    journal=files.workspace/'files_recovery.json'
    data=json.loads(journal.read_text())
    if tamper=='target':data['entries'][0]['target']=str(tmp_path/'outside.json')
    elif tamper=='staged':data['entries'][0]['staged']=str(tmp_path/'outside.new')
    else:Path(data['entries'][0]['staged']).write_bytes(b'CORRUPTED')
    journal.write_text(json.dumps(data),encoding='utf-8')
    state={'plan':[{'kind':'config','name':'user_config/preferences.json'}]}
    with pytest.raises(ValueError):restore._load_files(backup,files.workspace,state,None)
    assert target.read_bytes()==b'OLD'
