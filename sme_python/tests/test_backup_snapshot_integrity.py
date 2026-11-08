from dataclasses import replace
import io
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
from sme_gend.config.settings import Settings
from sme_gend.services import backup_restore_service as backup


def _local_backup(tmp_path, monkeypatch):
    source = tmp_path/'paths.json'
    source.write_text(json.dumps({'mysql_host':'synthetic.test','mysql_password':'SYNTHETIC_ONLY'}))
    monkeypatch.setattr(backup,'_collect_config_paths',lambda:[source])
    scope=backup.BackupScope(include_mysql=False,include_app_config=False,include_app_data=False,
        include_media_photos=False,include_media_grades=False,include_publications=False)
    destination=tmp_path/'backup.zip'
    result=backup.create_backup(Settings(database_path=tmp_path/'unused'),destination,scope=scope)
    return destination,result


def test_backup_records_and_verifies_every_member(tmp_path,monkeypatch):
    destination,manifest=_local_backup(tmp_path,monkeypatch)
    assert set(manifest.file_inventory)=={'user_config/paths.json'}
    assert backup.inspect_backup(destination).file_inventory==manifest.file_inventory


@pytest.mark.parametrize('kind',['modified','extra','missing','bad_inventory'])
def test_crc_valid_archive_tampering_is_rejected(tmp_path,monkeypatch,kind):
    destination,_=_local_backup(tmp_path,monkeypatch)
    with ZipFile(destination) as z:
        files={info.filename:z.read(info) for info in z.infolist()}
    if kind=='modified': files['user_config/paths.json']=b'CHANGED'
    if kind=='extra': files['user_config/extra.json']=b'{}'
    if kind=='missing': files.pop('user_config/paths.json')
    if kind=='bad_inventory':
        manifest=json.loads(files['manifest.json']);manifest['file_inventory']=[]
        files['manifest.json']=json.dumps(manifest).encode()
    with ZipFile(destination,'w') as z:
        for name,payload in files.items():z.writestr(name,payload)
    with pytest.raises(ValueError,match='[Ii]nventaire|altérée'):
        backup.inspect_backup(destination)


def test_old_manifest_without_inventory_stays_readable(tmp_path,monkeypatch):
    destination,_=_local_backup(tmp_path,monkeypatch)
    with ZipFile(destination) as z: files={n:z.read(n) for n in z.namelist()}
    raw=json.loads(files['manifest.json']);raw.pop('file_inventory')
    files['manifest.json']=json.dumps(raw).encode()
    with ZipFile(destination,'w') as z:
        for name,payload in files.items():z.writestr(name,payload)
    assert backup.inspect_backup(destination).file_inventory is None


def test_configured_sql_export_must_confirm_sidecar_snapshot(tmp_path,monkeypatch):
    monkeypatch.setattr(backup,'_export_mysql_sql',lambda *a,**k:('-- SQL',{}))
    with pytest.raises(RuntimeError,match='confirmés ensemble'):
        backup.create_backup(Settings(database_path=tmp_path/'unused'),tmp_path/'bad.zip')
    assert not (tmp_path/'bad.zip').exists()


def test_inventory_reads_windows_file_descriptors_without_changing_them(tmp_path):
    source=tmp_path/'config.json';source.write_bytes(b'{"synthetic":true}')
    destination=tmp_path/'windows.zip'
    with ZipFile(destination,'w') as archive:
        archive.write(source,arcname='app_data/config.json')
        info=archive.infolist()[0]
        # Python's Windows from_file descriptors retain this original spelling.
        info.orig_filename='app_data\\config.json'
        inventory=backup._backup_file_inventory(archive)
        assert set(inventory)=={'app_data/config.json'}
        assert info.orig_filename=='app_data\\config.json'
    with ZipFile(destination) as archive:
        backup._verify_backup_file_inventory(archive,inventory)
