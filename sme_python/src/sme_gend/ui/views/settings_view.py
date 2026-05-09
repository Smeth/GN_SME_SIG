"""Page Paramètres — chemins fichiers, mode SQLite, import bundle."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config import get_settings
from sme_gend.config.settings import Settings
from sme_gend.ui.paths_config_form import PathsConfigForm


class SettingsView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

        intro = QLabel(
            "<b>Fichiers, chemins et mode de données</b><br/>"
            "Accessible aussi via la barre latérale : <b>Maintenance & fichiers → Paramètres & données</b>. "
            "Les <b>données courantes</b> peuvent être dans un <b>fichier SQLite</b> (bundle) ; "
            "la base <b>Access</b> sert surtout à <b>importer</b> depuis l’ancien outil (.mdb). "
            "Après enregistrement, la configuration est <b>rechargée sans redémarrage</b>."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setObjectName("dashSubtitle")

        self._form = PathsConfigForm(self, settings=settings)

        self._btn_save = QPushButton("Enregistrer dans le profil utilisateur")
        self._btn_save.setObjectName("dashRefresh")
        self._btn_save.clicked.connect(self._on_save)

        inner = QWidget()
        inner_lay = QVBoxLayout(inner)
        inner_lay.setSpacing(16)
        inner_lay.setContentsMargins(0, 0, 8, 0)
        inner_lay.addWidget(intro)
        inner_lay.addWidget(self._form)
        inner_lay.addWidget(self._btn_save, alignment=Qt.AlignmentFlag.AlignLeft)
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
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._form.refresh_from_disk(settings)

    def _on_save(self) -> None:
        if self._form.try_save(confirm=True):
            win = self.window()
            if hasattr(win, "reload_runtime_configuration"):
                win.reload_runtime_configuration(show_success_dialog=False)
            else:
                self._settings = get_settings()
                self._form.refresh_from_disk(self._settings)
            QMessageBox.information(
                self,
                "Paramètres",
                "Les chemins ont été enregistrés et appliqués (rechargement des services et des vues).",
            )
