"""Fiches retirées de la liste — restauration depuis l'audit."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from sme_gend.config.settings import Settings
from sme_gend.ui.deleted_personnel_panel import DeletedPersonnelPanel
from sme_gend.ui.page_header import PageHeader


class DeletedPersonnelView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        header = PageHeader(
            "Fiches retirées de la liste",
            "Suppressions enregistrées avec motif de sortie (perte). "
            "La restauration réouvre le segment carrière sur le mois SME courant.",
        )
        self._panel = DeletedPersonnelPanel(settings, self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(header)
        lay.addWidget(self._panel, stretch=1)

    def apply_runtime_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._panel.rebind_settings(settings)

    def on_page_activated(self) -> None:
        self._panel.reload()
