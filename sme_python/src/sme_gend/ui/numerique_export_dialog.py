"""Dialogue export classeur situation numérique — choix du mois SME."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextEdit,
    QVBoxLayout,
)

from sme_gend.config.liste_generale_scope import (
    format_export_mois_choice_label,
    list_available_export_mois,
    resolve_export_mois,
)
from sme_gend.config.settings import Settings
from sme_gend.services.numerique_export_summary import (
    build_numerique_export_summary,
    format_numerique_export_preview_text,
)


class NumeriqueExportDialog(QDialog):
    """Sélection du mois SME et aperçu des effectifs avant génération Excel."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporter la situation numérique")
        self.setMinimumWidth(520)

        self._base_settings = settings
        self._chosen_mois: str | None = None

        intro = QLabel(
            "Choisissez le <b>mois SME</b> des données à inclure dans le classeur "
            "(liste générale, matrice, indisponibles, satellites, gains/pertes, "
            "détachés, liste des présents)."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._mois_combo = QComboBox()
        months = list_available_export_mois(settings)
        default = resolve_export_mois(settings)
        default_idx = 0
        for i, ym in enumerate(months):
            self._mois_combo.addItem(format_export_mois_choice_label(ym), ym)
            if ym == default:
                default_idx = i
        if months:
            self._mois_combo.setCurrentIndex(default_idx)
        self._mois_combo.currentIndexChanged.connect(self._refresh_preview)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(220)
        self._preview.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 11px;"
        )

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Générer…")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(QLabel("Mois SME :"))
        lay.addWidget(self._mois_combo)
        lay.addWidget(QLabel("Résumé :"))
        lay.addWidget(self._preview)
        lay.addWidget(bb)

        self._refresh_preview()

    def chosen_export_mois(self) -> str | None:
        return self._chosen_mois

    def accept(self) -> None:
        self._chosen_mois = self._mois_combo.currentData()
        super().accept()

    def _refresh_preview(self) -> None:
        ym = self._mois_combo.currentData()
        summary = build_numerique_export_summary(self._base_settings, export_mois=ym)
        self._preview.setPlainText(format_numerique_export_preview_text(summary))
