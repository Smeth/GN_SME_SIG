"""Page Paramètres — chemins fichiers, MySQL, import Access."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config import get_settings
from sme_gend.config.settings import Settings
from sme_gend.ui.deleted_personnel_panel import DeletedPersonnelPanel
from sme_gend.ui.paths_config_form import PathsConfigForm


class SettingsView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings


        self._form = PathsConfigForm(self, settings=settings)
        self._deleted_panel = DeletedPersonnelPanel(settings, self)

        self._btn_save = QPushButton("Enregistrer les paramètres")
        self._btn_save.setObjectName("primaryButton")
        self._btn_save.clicked.connect(self._on_save)

        btn_bar = QFrame()
        btn_bar.setObjectName("settingsActionBar")
        btn_row = QHBoxLayout(btn_bar)
        btn_row.setContentsMargins(0, 8, 0, 0)
        btn_row.addWidget(self._btn_save)
        btn_row.addStretch()

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(5)
        inner_lay.setContentsMargins(0, 0, 8, 0)
        inner_lay.addWidget(self._form, stretch=1)
        inner_lay.addWidget(self._deleted_panel)
        inner_lay.addWidget(btn_bar)
        inner_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.addWidget(scroll)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._form.refresh_from_disk(settings)
        self._deleted_panel.rebind_settings(settings)

    def _on_save(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if not require_permission(self, "system.settings"):
            return
        if self._form.try_save(confirm=True):
            win = self.window()
            if hasattr(win, "reload_runtime_configuration"):
                if not win.reload_runtime_configuration(show_success_dialog=False):
                    QMessageBox.warning(
                        self, "Paramètres enregistrés",
                        "Les paramètres ont été enregistrés, mais leur application a échoué. "
                        "Vérifiez la configuration avant de poursuivre.",
                    )
                    return
            else:
                self._settings = get_settings()
                self._form.refresh_from_disk(self._settings)
            QMessageBox.information(
                self,
                "Paramètres",
                "Les chemins ont été enregistrés et appliqués (rechargement des services et des vues).",
            )
