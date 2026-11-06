from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox

from sme_gend.services import import_replacement as service
from sme_gend.ui import import_mois_dialog as ui


@pytest.mark.parametrize('preserve', [True, False])
def test_import_options_select_mode_and_default_to_preservation(monkeypatch, preserve, tmp_path):
    from PySide6.QtGui import QFontDatabase, QFont
    from sme_gend.ui.theme import apply_app_theme
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    apply_app_theme(app, 'light')
    monkeypatch.setattr(ui, 'load_sme_context', lambda: SimpleNamespace(mois_actif='2026-09'))
    def execute(dialog):
        combo = dialog.findChild(QComboBox, 'importReplacementMode')
        assert combo.currentData() is True
        combo.setCurrentIndex(0 if preserve else 1)
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(tmp_path / 'import_modes.png'))
        print('Import modes render:', tmp_path / 'import_modes.png')
        dialog.findChild(QDialogButtonBox).accepted.emit()
        return dialog.result()
    monkeypatch.setattr(QDialog, 'exec', execute)
    assert ui.pick_import_options().preserve_manual is preserve


def test_full_mode_requires_admin(monkeypatch):
    monkeypatch.setattr('sme_gend.services.workflow_permissions.session_can_perform_action', lambda _: True)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: False)
    service.authorize_replacement(True)
    with pytest.raises(PermissionError):
        service.authorize_replacement(False)


def test_full_bundle_requires_preview_before_backup_or_database(monkeypatch):
    from sme_gend.services import mysql_bundle_import as bundle
    monkeypatch.setattr(service, 'authorize_replacement', lambda _: None)
    backup = MagicMock()
    monkeypatch.setattr('sme_gend.services.backup_restore_service.ensure_recent_backup', backup)
    with pytest.raises(ValueError, match='aperçu'):
        bundle.import_full_mysql_bundle_from_access(object(), object(), mois='2026-09', preserve_manual=False)
    backup.assert_not_called()


@pytest.mark.parametrize('preserve,accepted', [(True, True), (False, True), (False, False)])
def test_wizard_preview_and_mode_reach_the_importer(monkeypatch, tmp_path, preserve, accepted):
    from PySide6.QtWidgets import QWidget
    from sme_gend.config.settings import Settings
    from sme_gend.ui import import_liste_generale_wizard as wizard
    app = QApplication.instance() or QApplication([])
    path = tmp_path / 'source.mdb'
    path.write_bytes(b'SYNTHETIC')
    settings = Settings(database_path=Path('unused'))
    preview = service.ImportReplacementPreview('2026-09', preserve, (), 'test',
              (('carriere_situation', 10), ('parcours_stage', 4)))
    monkeypatch.setattr(wizard.QFileDialog, 'getOpenFileName', lambda *a: (str(path), ''))
    monkeypatch.setattr('sme_gend.services.access_odbc.preflight_access_file', lambda *a, **kw: (True, 'OK'))
    monkeypatch.setattr(wizard, 'pick_import_options', lambda *a: ui.ImportOptions('2026-09', False, None, preserve))
    monkeypatch.setattr('sme_gend.services.mysql_bundle_import.preview_access_import', lambda *a: {'counts': {}, 'warnings': []})
    monkeypatch.setattr(service, 'preview_import_replacement', lambda *a, **kw: preview)
    monkeypatch.setattr(wizard, 'run_with_progress', lambda p, t, label, fn: fn())
    monkeypatch.setattr(wizard, 'run_with_determinate_progress', lambda p, t, n, fn: fn(lambda *a: None))
    monkeypatch.setattr(wizard, 'load_sme_context', lambda: SimpleNamespace(mois_actif='2026-09'))
    confirm = MagicMock(return_value=accepted)
    monkeypatch.setattr(wizard, 'confirm_yes_no', confirm)
    importer = MagicMock(return_value=SimpleNamespace(mysql_bundle_result=None, data_archive=None, warnings=[]))
    monkeypatch.setattr('sme_gend.services.sme_month_closure.import_existing_db_for_month', importer)
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.show_scrollable_html_message', lambda *a: None)
    parent = QWidget()
    assert wizard.run_import_liste_generale_from_access(parent, settings) == accepted
    assert 'Stages : 4' in confirm.call_args.args[2]
    assert ('Conserver les saisies' if preserve else 'Remplacer entièrement') in confirm.call_args.args[2]
    if accepted:
        assert importer.call_args.kwargs['preserve_manual'] is preserve
        assert importer.call_args.kwargs['replacement_preview'] is preview
    else:
        importer.assert_not_called()
    parent.close()
