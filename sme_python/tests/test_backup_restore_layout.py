from dataclasses import replace
from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QBoxLayout, QDialog, QInputDialog, QMessageBox
from sme_gend.config.settings import Settings
from sme_gend.ui.views import backup_restore_view as ui


@pytest.fixture(scope='module')
def app():
    app=QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI',10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app,'light')
    return app


@pytest.fixture
def view(app,monkeypatch,tmp_path):
    monkeypatch.setattr(ui.BackupRestoreView,'_refresh_health_async',lambda *a,**kw:None)
    monkeypatch.setattr(ui.BackupRestoreView,'_load_mysql_tables_async',lambda *a:None)
    monkeypatch.setattr(ui,'session_can_perform_action',lambda key:True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:True)
    widget=ui.BackupRestoreView(Settings(database_path=tmp_path/'unused',mysql_host='synthetic.invalid',mysql_database='test_only'))
    yield widget
    widget.close();widget.deleteLater();app.processEvents()


@pytest.mark.parametrize('width,height',[(1280,800),(760,700)])
def test_exactly_two_tabs_keep_data_actions_visible_without_scrolling(view,app,tmp_path,width,height):
    view.resize(width,height);view.show();app.processEvents()
    assert view._tabs.count()==2
    assert [view._tabs.tabText(i) for i in range(2)]==['Sauvegarde et restauration','Suppression et réinitialisation']
    assert not view._btn_empty_month.isVisible() and not view._btn_reset.isVisible()
    view._tabs.setCurrentIndex(1);app.processEvents()
    viewport=view._tabs.currentWidget().viewport()
    for button in (view._btn_empty_month,view._btn_reset):
        assert button.isVisible() and button.isEnabled()
        top=button.mapTo(viewport,QPoint(0,0))
        assert top.y()>=0 and top.y()+button.height()<=viewport.height()
    assert view._tabs.currentWidget().horizontalScrollBar().maximum()==0
    assert view.grab().save(str(tmp_path/f'backup_data_{width}.png'))
    view._tabs.setCurrentIndex(0);app.processEvents()
    assert not view._backup_scope.isVisible() and not view._restore_scope.isVisible()
    expected=QBoxLayout.Direction.LeftToRight if width==1280 else QBoxLayout.Direction.TopToBottom
    assert view._columns._layout.direction()==expected
    assert view._tabs.currentWidget().horizontalScrollBar().maximum()==0
    assert view.grab().save(str(tmp_path/f'backup_operations_{width}.png'))


@pytest.mark.parametrize('admin,backup,restore',[(True,True,False),(True,False,True),(False,True,True),(False,False,False)])
def test_buttons_follow_permissions_and_destructive_actions_also_require_admin(view,monkeypatch,admin,backup,restore):
    monkeypatch.setattr(ui,'session_can_perform_action',lambda key: {'system.backup':backup,'system.restore':restore}.get(key,False))
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:admin)
    view.on_page_activated()
    assert view._btn_backup.isEnabled()==backup
    assert view._btn_backup_quick.isEnabled()==backup
    assert view._btn_partial.isEnabled()==backup
    assert view._btn_backup_month.isEnabled()==backup
    assert view._btn_restore.isEnabled()==restore
    assert view._btn_reset.isEnabled()==(admin and restore)
    assert view._btn_empty_month.isEnabled()==(admin and restore)


@pytest.mark.parametrize('admin,restore',[(False,True),(True,False)])
def test_programmatic_destructive_entry_refuses_before_dialog_or_service(view,monkeypatch,admin,restore):
    monkeypatch.setattr(ui,'session_can_perform_action',lambda key:restore)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role',lambda:admin)
    warning=Mock();monkeypatch.setattr(QMessageBox,'warning',warning)
    execute=Mock();monkeypatch.setattr('sme_gend.ui.empty_month_dialog.run_empty_month_dialog',execute)
    reset=Mock();monkeypatch.setattr('sme_gend.services.application_reset.reset_application',reset)
    view._on_empty_month();view._on_reset_application()
    assert warning.call_count==2
    execute.assert_not_called();reset.assert_not_called()


def test_month_action_uses_shared_confirmation_flow_and_logs_only_success(view,monkeypatch):
    action=Mock(return_value=False)
    monkeypatch.setattr('sme_gend.ui.empty_month_dialog.run_empty_month_dialog',action)
    view._btn_empty_month.click()
    action.assert_called_once_with(view,view._settings)
    assert not view._log.toPlainText()
    action.return_value=True
    view._btn_empty_month.click()
    assert 'Données du mois supprimées' in view._log.toPlainText()


@pytest.mark.parametrize('accepted,typed',[(False,''),(True,'another.invalid:3306/wrong')])
def test_reset_does_not_run_without_exact_target_confirmation(view,monkeypatch,accepted,typed):
    from sme_gend.ui.application_reset_dialog import ApplicationResetDialog
    monkeypatch.setattr(ApplicationResetDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QInputDialog,'getText',lambda *a,**kw:(typed,accepted))
    execute=Mock();monkeypatch.setattr('sme_gend.services.application_reset.reset_application',execute)
    view._on_reset_application()
    execute.assert_not_called()


def test_selecting_archive_from_elsewhere_opens_operations_and_advanced_restore(view,monkeypatch,tmp_path):
    from sme_gend.services.backup_restore_service import BackupManifest
    archive=tmp_path/'synthetic.zip'
    with ZipFile(archive,'w') as z:z.writestr('mysql.sql','-- synthetic')
    manifest=BackupManifest(4,'2026-11-08','test_only','synthetic.invalid',{},[])
    monkeypatch.setattr(ui,'inspect_backup',lambda path:manifest)
    view._tabs.setCurrentIndex(1)
    view.select_archive_for_restore(archive)
    assert view._tabs.currentIndex()==0
    assert view._restore_advanced_toggle.isChecked()
    assert view._partial_restore_zip==archive


def test_rebinding_updates_the_database_target(view,monkeypatch):
    settings=replace(view._settings,mysql_host='new.invalid',mysql_database='new_copy')
    view.rebind_settings(settings)
    assert view._target_lbl.text()=='Base concernée : new.invalid:3306/new_copy'


@pytest.mark.parametrize('width',[1280,760])
def test_partial_options_remain_reachable_without_horizontal_scrolling(view,app,width):
    view.resize(width,800);view.show();app.processEvents()
    view._backup_advanced_toggle.setChecked(True)
    view._restore_advanced_toggle.setChecked(True)
    app.processEvents()
    assert view._backup_scope.isVisible() and view._restore_scope.isVisible()
    assert view._tabs.currentWidget().horizontalScrollBar().maximum()==0


@pytest.mark.parametrize('action',['quick','full','partial'])
def test_failed_or_cancelled_backup_does_not_report_success_or_prune_existing_archives(view,monkeypatch,tmp_path,action):
    monkeypatch.setattr(ui,'run_with_progress',lambda *a,**kw:None)
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',lambda *a,**kw:(str(tmp_path/'backup.zip'),'zip'))
    info=Mock();prune=Mock()
    monkeypatch.setattr(QMessageBox,'information',info)
    monkeypatch.setattr(ui,'prune_old_backups',prune)
    if action=='quick':view._on_backup_quick()
    elif action=='full':view._on_backup()
    else:view._on_partial_backup(to_default_dir=True)
    info.assert_not_called();prune.assert_not_called()
    assert view._last_backup is None and not view._log.toPlainText()
