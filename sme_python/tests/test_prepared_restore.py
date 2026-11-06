"""Synthetic files and validation only; native database cases are separate."""
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import pytest
from sme_gend.services.mysql_restore_staging import validate_staged_statement
from sme_gend.services.restore_files import RestoreFiles


@pytest.mark.parametrize("ddl",[
    "CREATE TABLE t (id INT) ENGINE=MyISAM;",
    "CREATE TABLE t (id INT) ENGINE=InnoDB DATA DIRECTORY='/outside';",
    "CREATE TABLE t (id INT) ENGINE=InnoDB AS SELECT * FROM outside;",
    "CREATE TABLE t (id INT) ENGINE=FEDERATED CONNECTION='outside';",
    "CREATE TABLE t (id INT,FOREIGN KEY(id) REFERENCES other.t(id)) ENGINE=InnoDB;",
    "CREATE TABLE t (id INT,FOREIGN KEY(id) REFERENCES `other`.`t`(id)) ENGINE=InnoDB;",
    "CREATE TABLE t (id INT) ENGINE=InnoDB TABLESPACE outside;",
])
def test_archive_ddl_cannot_escape_the_prepared_database(ddl):
    with pytest.raises(ValueError):validate_staged_statement(ddl)


def test_supported_create_handles_literal_parentheses_and_local_foreign_keys():
    validate_staged_statement("CREATE TABLE `t` (`id` INT,`label` VARCHAR(50) DEFAULT 'a) REFERENCES other.t',CONSTRAINT `fk` FOREIGN KEY (`id`) REFERENCES `parent` (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")


def test_staging_files_does_not_publish_or_truncate_a_destination(tmp_path):
    original=tmp_path/'photo.png';original.write_bytes(b'OLD')
    missing=tmp_path/'missing.png'
    files=RestoreFiles(tmp_path/'private')
    files.add(original,BytesIO(b'NEW'))
    files.add(missing,BytesIO(b'OTHER'))
    assert original.read_bytes()==b'OLD' and not missing.exists()
    assert not list(tmp_path.glob('.sme-restore-probe-*'))
    files.publish();files.rollback()
    assert original.read_bytes()==b'OLD' and not missing.exists()


def test_file_recovery_does_not_overwrite_a_later_external_change(tmp_path):
    original=tmp_path/'photo.png';original.write_bytes(b'OLD')
    files=RestoreFiles(tmp_path/'private');files.add(original,BytesIO(b'NEW'))
    files.publish();original.write_bytes(b'EXTERNAL CHANGE')
    with pytest.raises(RuntimeError,match='Retour'):files.rollback()
    assert original.read_bytes()==b'EXTERNAL CHANGE'
    assert json.loads((files.workspace/'files_recovery.json').read_text())['phase']=='recovery_required'


def test_corrupted_recovery_copy_is_not_restored_silently(tmp_path):
    original=tmp_path/'photo.png';original.write_bytes(b'OLD')
    files=RestoreFiles(tmp_path/'private');files.add(original,BytesIO(b'NEW'))
    files.publish();Path(files.entries[0]['original']).write_bytes(b'BROKEN')
    with pytest.raises(RuntimeError):files.rollback()
    assert original.read_bytes()==b'NEW'


@pytest.mark.parametrize('method',['_on_restore_full','_on_restore_partial'])
def test_restore_entry_points_require_the_existing_backup_permission(monkeypatch,method):
    from sme_gend.ui.views import backup_restore_view as view
    monkeypatch.setattr(view,'session_can_perform_action',lambda permission:False)
    warn=MagicMock();monkeypatch.setattr(view.QMessageBox,'warning',warn)
    monkeypatch.setattr(view.QFileDialog,'getOpenFileName',lambda *a:pytest.fail('No dialog before permission'))
    getattr(view.BackupRestoreView,method)(SimpleNamespace())
    warn.assert_called_once()


def test_empty_failed_backup_preserves_an_existing_zip(monkeypatch,tmp_path):
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.config.settings import Settings
    destination=tmp_path/'backup.zip';destination.write_bytes(b'PREVIOUS VALID ARCHIVE')
    scope=backup.BackupScope(False,None,False,False,False,False,False)
    with pytest.raises(ValueError,match='vide'):
        backup.create_backup(Settings(database_path=tmp_path/'unused'),destination,scope=scope)
    assert destination.read_bytes()==b'PREVIOUS VALID ARCHIVE'
    assert not list(tmp_path.glob('.sme-*'))



def test_restore_lock_is_identical_for_aliases_of_the_same_mysql_server():
    from sme_gend.config.settings import Settings
    from sme_gend.services.mysql_restore_staging import maintenance_lock_name
    from dataclasses import replace
    source=Settings(database_path=Path('unused'),mysql_host='127.0.0.1',mysql_database='SMEGEND')
    alias=replace(source,mysql_host='server.internal',mysql_port=3307,mysql_database='smegend')
    assert maintenance_lock_name(source)==maintenance_lock_name(alias)
