from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from sme_gend.services.application_reset import reset_application, reset_target, ResetOptions


@pytest.mark.parametrize('admin,restore',[(False,True),(True,False),(False,False)])
def test_reset_is_guarded_before_backup(monkeypatch,admin,restore):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:admin)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:restore)
    backup=Mock()
    monkeypatch.setattr('sme_gend.services.backup_restore_service.create_backup_to_default_dir',backup)
    with pytest.raises(PermissionError):
        reset_application(object(),ResetOptions(),confirmation='anything')
    backup.assert_not_called()


def test_reset_refuses_wrong_target_before_backup(monkeypatch):
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda *a:True)
    settings=SimpleNamespace(mysql_host='host',mysql_port=3306,mysql_database='database')
    with pytest.raises(ValueError,match='confirmation'):
        reset_application(settings,ResetOptions(),confirmation='other database')


def test_reset_choices_and_new_account_visibility(tmp_path):
    from sme_gend.ui.application_reset_dialog import ApplicationResetDialog
    app=QApplication.instance() or QApplication([])
    dialog=ApplicationResetDialog('host:3306/database')
    assert dialog.options()==ResetOptions(True,True)
    assert dialog.credentials.isHidden()
    dialog.keep_users.setChecked(False)
    assert not dialog.credentials.isHidden()
    assert dialog.options()==ResetOptions(False,True)
    dialog.keep_documents.setChecked(False)
    assert dialog.options()==ResetOptions(False,False)
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(tmp_path/'reset_dialog.png'))
    dialog.keep_users.setChecked(True)
    assert dialog.options()==ResetOptions(True,False)
    dialog.deleteLater()
    app.processEvents()


def test_reset_rejects_mismatched_new_password(monkeypatch):
    from sme_gend.ui.application_reset_dialog import ApplicationResetDialog
    from PySide6.QtWidgets import QMessageBox
    app=QApplication.instance() or QApplication([])
    dialog=ApplicationResetDialog('host:3306/database')
    dialog.keep_users.setChecked(False)
    dialog.username.setText('replacement')
    dialog.password.setText('Test-Secret-123')
    dialog.repeat.setText('Other-Secret-123')
    warning=Mock()
    monkeypatch.setattr(QMessageBox,'warning',warning)
    dialog._validate()
    warning.assert_called_once()
    assert dialog.result()!=QDialog.DialogCode.Accepted
    dialog.deleteLater()
    app.processEvents()


def test_file_cleanup_is_recoverable(tmp_path):
    from sme_gend.services.application_reset_files import ResetLocalFiles
    original=tmp_path/'monthly.mdb';original.write_bytes(b'MONTH')
    files=ResetLocalFiles(tmp_path/'recovery',[original])
    files.remove()
    assert not original.exists()
    files.rollback()
    assert original.read_bytes()==b'MONTH'


def test_file_cleanup_refuses_changed_file(tmp_path):
    from sme_gend.services.application_reset_files import ResetLocalFiles
    original=tmp_path/'monthly.mdb';original.write_bytes(b'MONTH')
    files=ResetLocalFiles(tmp_path/'recovery',[original])
    original.write_bytes(b'CHANGED')
    with pytest.raises(RuntimeError,match='change'):
        files.remove()
    assert original.read_bytes()==b'CHANGED'


def test_reset_entry_point_obeys_restore_permissions(monkeypatch,tmp_path):
    from sme_gend.ui.views import backup_restore_view as module
    from sme_gend.config.settings import Settings
    app=QApplication.instance() or QApplication([])
    view=module.BackupRestoreView(Settings(database_path=tmp_path/'unused.mdb'))
    monkeypatch.setattr(view,'_refresh_health_async',lambda **kw:None)
    monkeypatch.setattr(view,'_load_mysql_tables_async',lambda:None)
    monkeypatch.setattr(module,'session_can_perform_action',lambda *a:False)
    view.on_page_activated()
    assert not view._btn_reset.isEnabled()
    assert not view._btn_restore_month.isEnabled()
    assert not view._btn_backup_month.isEnabled()
    monkeypatch.setattr(module,'session_can_perform_action',lambda *a:True)
    view.on_page_activated()
    assert view._btn_reset.isEnabled()
    assert view._btn_restore_month.isEnabled()
    assert view._btn_backup_month.isEnabled()
    view.deleteLater()
    app.processEvents()
