"""La configuration du menu et celle du démarrage utilisent le même contrat."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from sme_gend.config.settings import Settings
from sme_gend.ui import config_paths_dialog
from sme_gend.ui.shell_window import ShellWindow


@pytest.mark.parametrize("accepted", [False, True])
@pytest.mark.parametrize("has_drafts", [False, True])
def test_configuration_menu_passes_settings_and_reloads_only_after_save(monkeypatch, accepted, has_drafts):
    app = QApplication.instance() or QApplication([])
    settings = Settings(database_path=Path("unused.mdb"))
    # Conserver le vrai constructeur du dialogue : un mock masquerait le TypeError.
    form = MagicMock(side_effect=lambda parent, **kwargs: QWidget(parent))
    monkeypatch.setattr(config_paths_dialog, "PathsConfigForm", form)
    result = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    monkeypatch.setattr(config_paths_dialog.ConfigPathsDialog, "exec", lambda self: result)
    parent = QWidget()
    parent._settings = settings
    parent._has_unsaved_changes = lambda: has_drafts
    parent.reload_runtime_configuration = MagicMock()
    warning = MagicMock()
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.warning", warning)

    ShellWindow._open_paths_config(parent)

    if has_drafts:
        form.assert_not_called()
        parent.reload_runtime_configuration.assert_not_called()
        warning.assert_called_once()
        parent.close()
        return
    assert form.call_args.kwargs["settings"] is settings
    if accepted:
        parent.reload_runtime_configuration.assert_called_once_with(show_success_dialog=True)
    else:
        parent.reload_runtime_configuration.assert_not_called()
    parent.close()
