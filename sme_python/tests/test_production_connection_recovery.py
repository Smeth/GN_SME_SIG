from types import SimpleNamespace
from unittest.mock import Mock
import json

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from sme_gend.config.settings import _resolve_mysql_connection
from sme_gend.config.user_paths import UserPathsOverrides
from sme_gend.services.connection_config_audit import record_connection_config_saved
from sme_gend.services.mysql_health import format_mysql_error_for_user
from sme_gend.ui.startup_maintenance import wait_for_startup_maintenance


@pytest.mark.parametrize('source', ['file', 'environment'])
def test_password_is_used_exactly_as_entered(monkeypatch, source):
    password = '  secret  '
    monkeypatch.setenv('SME_MYSQL_PASSWORD', password)
    user = UserPathsOverrides(mysql_host='production', mysql_password=password) if source == 'file' else UserPathsOverrides()
    assert _resolve_mysql_connection(user)[4] == password


def test_missing_password_message():
    assert "aucun mot de passe" in format_mysql_error_for_user('(1045, using password: NO)')


@pytest.mark.parametrize('unavailable', [False, True])
def test_local_configuration_has_durable_trace_and_uses_new_target(monkeypatch, tmp_path, unavailable):
    settings = SimpleNamespace(mysql_host='new-production', mysql_port=3307, mysql_database='prod', mysql_password='NEVER_LOG')
    monkeypatch.setattr('sme_gend.config.user_paths.user_application_data_dir', lambda: tmp_path)
    monkeypatch.setattr('sme_gend.services.auth_context.resolve_audit_actor', lambda: 'operator')
    repo = Mock()
    if unavailable:
        repo.append_audit.side_effect = RuntimeError('maintenance')
    factory = Mock(return_value=repo)
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository', factory)
    warning = record_connection_config_saved(settings)
    factory.assert_called_once_with(settings)
    text = (tmp_path / 'connection_config_audit.jsonl').read_text(encoding='utf-8')
    assert json.loads(text)['mysql_host'] == 'new-production'
    assert 'NEVER_LOG' not in text
    assert bool(warning) == unavailable


@pytest.mark.parametrize('retry', [False, True])
def test_startup_waits_without_clearing_maintenance(monkeypatch, retry):
    app = QApplication.instance() or QApplication([])
    state = dict(active=True, started_at='2026-11-03', reason='reset')
    status = Mock(side_effect=[state, dict(active=False)])
    monkeypatch.setattr('sme_gend.services.database_maintenance.maintenance_status', status)
    warning = Mock(return_value=QMessageBox.StandardButton.Retry if retry else QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    settings = SimpleNamespace(mysql_host='prod', mysql_port=3306, mysql_database='prod')
    assert wait_for_startup_maintenance(settings) == retry
    assert status.call_count == (2 if retry else 1)


def test_startup_refuses_unreadable_maintenance_state(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr('sme_gend.services.database_maintenance.maintenance_status', Mock(side_effect=RuntimeError('offline')))
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    assert wait_for_startup_maintenance(object()) is False


def test_form_accepts_already_saved_profile_when_central_audit_unavailable(monkeypatch):
    from sme_gend.ui.paths_config_form import PathsConfigForm
    app = QApplication.instance() or QApplication([])
    form = Mock()
    for field in ('_db', '_tpl', '_tnum', '_tdet', '_arch', '_media'):
        getattr(form, field).text.return_value = ''
    form._mysql_host.text.return_value = 'new-prod'
    form._mysql_database.text.return_value = 'prod'
    form._mysql_user.text.return_value = 'operator'
    form._mysql_password.text.return_value = 'secret'
    form._validated_mysql_port.return_value = 3306
    form._chk_startup.isEnabled.return_value = False
    settings = object()
    form._build_mysql_settings.return_value = settings
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.MySQLConnection.test_connection', lambda self: (True, 'ok'))
    save = Mock()
    monkeypatch.setattr('sme_gend.ui.paths_config_form.save_user_paths', save)
    audit = Mock(return_value='Paramètres enregistrés ; maintenance en cours.')
    monkeypatch.setattr('sme_gend.services.connection_config_audit.record_connection_config_saved', audit)
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    assert PathsConfigForm.try_save(form, confirm=False) is True
    assert save.call_args.kwargs['mysql_password'] == 'secret'
    audit.assert_called_once_with(settings)
