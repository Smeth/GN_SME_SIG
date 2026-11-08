from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QDialog
from sme_gend.services.application_reset import reset_application, reset_target, ResetOptions


def test_large_table_is_deleted_in_bounded_batches_without_committing():
    from sme_gend.services.application_reset import _delete_reset_table
    from unittest.mock import MagicMock
    cursor=MagicMock()
    amounts=iter((1000,1000,17))
    def execute(*args):cursor.rowcount=next(amounts)
    cursor.execute.side_effect=execute
    assert _delete_reset_table(cursor,'carriere_situation',keep_document_history=True)==2017
    assert cursor.execute.call_count==3
    assert all('LIMIT 1000' in call.args[0] for call in cursor.execute.call_args_list)
    assert all('TRUNCATE' not in call.args[0] for call in cursor.execute.call_args_list)


def test_document_history_remains_excluded_from_every_delete_batch():
    from sme_gend.services.application_reset import _delete_reset_table
    from unittest.mock import MagicMock
    cursor=MagicMock();cursor.rowcount=2
    assert _delete_reset_table(cursor,'audit_journal',keep_document_history=True)==2
    assert "module <> 'doc_gen'" in cursor.execute.call_args.args[0]


def test_reset_timeout_message_names_the_phase_and_retains_the_safety_backup(caplog):
    import pymysql
    from sme_gend.services.application_reset import _reset_step
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    original=pymysql.OperationalError(2013,'Lost connection to MySQL server during query (timed out)')
    with pytest.raises(pymysql.OperationalError) as raised:
        with _reset_step('suppression de la table carriere_situation',backup='synthetic_backup.zip'):
            raise original
    assert raised.value is original
    message=format_mysql_error_for_user(original)
    assert 'carriere_situation' in message and 'délai' in message and 'synthetic_backup.zip' in message
    assert 'code=2013' in caplog.text


def test_closed_connection_message_is_not_an_empty_mysql_tuple():
    import pymysql
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    for value in (pymysql.InterfaceError(0,''),"(0, '')"):
        assert 'déjà fermée' in format_mysql_error_for_user(value)


def test_uncertain_commit_message_does_not_invite_retry():
    import pymysql
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    error=pymysql.OperationalError(2013,'Lost connection to MySQL')
    error.reset_commit_uncertain=True
    assert 'ne relancez pas' in format_mysql_error_for_user(error)


def test_failed_file_recovery_message_keeps_the_safety_archive_and_maintenance():
    from sme_gend.services.mysql_health import format_mysql_error_for_user
    error=OSError('synthetic file recovery failure')
    error.reset_stage='récupération des fichiers'
    error.reset_recovery_required=True
    error.reset_safety_backup='synthetic.zip'
    message=format_mysql_error_for_user(error)
    assert 'maintenance est conservée' in message and 'synthetic.zip' in message


def test_schema_unlock_failure_after_confirmed_reset_returns_warning_not_a_second_reset(monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services import application_reset as module
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda key:True)
    @contextmanager
    def failed_cleanup(settings):
        yield
        raise RuntimeError('synthetic lock release failure')
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.schema_metadata_lock',failed_cleanup)
    result={'warnings':[],'backup':'synthetic.zip'}
    execute=Mock(return_value=result)
    monkeypatch.setattr(module,'_reset_application_locked',execute)
    settings=SimpleNamespace(mysql_host='test.invalid',mysql_port=33329,mysql_database='synthetic')
    assert reset_application(settings,ResetOptions(),confirmation=reset_target(settings)) is result
    execute.assert_called_once()
    assert 'Ne recommencez pas' in result['warnings'][0]


def test_maintenance_cleanup_after_confirmed_reset_is_an_explicit_warning(monkeypatch):
    from contextlib import contextmanager
    from sme_gend.services.application_reset import _reset_maintenance
    @contextmanager
    def failed_cleanup(*a,**kw):
        yield object()
        raise RuntimeError('synthetic maintenance cleanup failure')
    monkeypatch.setattr('sme_gend.services.database_maintenance.centralized_maintenance',failed_cleanup)
    warnings=[];confirmed={}
    with _reset_maintenance(object(),warnings,confirmed):confirmed['backup']='synthetic.zip'
    assert len(warnings)==1 and 'Ne recommencez pas' in warnings[0]


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
