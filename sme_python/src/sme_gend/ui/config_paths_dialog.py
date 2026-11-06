"""Dialogue : configuration des chemins (fichier utilisateur)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout

from sme_gend.config.settings import Settings
from sme_gend.ui.paths_config_form import PathsConfigForm


class ConfigPathsDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        settings: Settings | None = None,
        focus_database: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuration des données (MySQL)")
        self.setModal(True)
        self.resize(720, 520)

        self._form = PathsConfigForm(self, settings=settings, focus_database=focus_database)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self._form)
        lay.addWidget(buttons)

    def _on_accept(self) -> None:
        if self._form.try_save(confirm=True):
            self.accept()
