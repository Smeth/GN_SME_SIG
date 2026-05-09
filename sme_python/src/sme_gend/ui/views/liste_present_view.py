"""Liste des présents — aperçu HTML + export Excel."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QShowEvent
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.liste_present_service import ListePresentService
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no


class ListePresentView(QWidget):
    def __init__(self, service: ListePresentService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._open_layout: Callable[[], None] | None = None
        self._loaded = False

        self._web = QWebEngineView()

        self._btn_refresh = QPushButton("Actualiser l'aperçu")
        self._btn_refresh.clicked.connect(lambda: self._refresh(confirm=False))

        self._btn_export = QPushButton("Exporter vers Excel…")
        self._btn_export.clicked.connect(self._export)

        self._btn_layout = QPushButton("Configurer l'ordre d'affichage…")
        self._btn_layout.setToolTip(
            "Ouvre la page « Mise en page liste » : glisser-déposer des structures, "
            "ordre des subdivisions d'arme par section, titres optionnels."
        )
        self._btn_layout.clicked.connect(self._on_open_layout)
        self._btn_layout.setVisible(False)

        self._info = QLabel(
            "Données : table <b>ListeGenerale</b> (SQLite ou Access selon la configuration), lignes dont la position "
            "administrative (Padmin) est « présent » ou stage interne, ordre des sections selon "
            "<code>liste_present_layout.json</code>. "
            "<b>L'ordre des blocs (structures puis subdivisions)</b> se règle sur la page "
            "<b>Mise en page liste</b> (menu latéral Effectifs & listes, ou bouton ci-dessous). "
            "Aperçu aligné sur la <b>maquette</b> <code>resultat.xlsx</code> (18 colonnes, "
            "mêmes rubriques d'en-tête que la feuille <b>Liste</b>), titres par structure puis sous-blocs "
            "par <b>subdivision d'arme</b> (ordre alphabétique ou liste par structure "
            "dans la mise en page). La recherche filtre les personnes "
            "affichées sans modifier la base."
        )
        self._info.setWordWrap(True)
        self._info.setTextFormat(Qt.TextFormat.RichText)

        self._search = QLineEdit()
        self._search.setPlaceholderText(
            "Filtrer (nom, grade, structure, matricule…) — sur les présents uniquement"
        )

        # Debounce : attend 350 ms d'inactivité avant de relancer le rendu
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(350)
        self._search_timer.timeout.connect(lambda: self._refresh(confirm=False))
        self._search.textChanged.connect(lambda: self._search_timer.start())

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._btn_export)
        bar.addWidget(self._btn_layout)
        bar.addStretch()

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Recherche :"))
        search_row.addWidget(self._search, stretch=1)

        lay = QVBoxLayout(self)
        lay.addWidget(self._info)
        lay.addLayout(bar)
        lay.addLayout(search_row)
        lay.addWidget(self._web, stretch=1)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._loaded:
            self._refresh(confirm=False)

    def mark_dirty(self) -> None:
        """Force un rechargement au prochain affichage (config ou base modifiée)."""
        self._loaded = False

    def apply_runtime_after_config_change(self) -> None:
        self._loaded = False
        self._refresh(confirm=False)

    def set_open_layout_editor(self, fn: Callable[[], None] | None) -> None:
        """Branche l'ouverture de « Mise en page liste » (appelé par la fenêtre principale)."""
        self._open_layout = fn
        self._btn_layout.setVisible(fn is not None)

    def _on_open_layout(self) -> None:
        if self._open_layout:
            self._open_layout()

    def _refresh(self, *, confirm: bool = False) -> None:
        if confirm:
            if not confirm_yes_no(
                self,
                "Actualiser l'aperçu",
                "Recharger l'aperçu HTML depuis la base configurée ?",
                default_no=False,
            ):
                return
        needle = self._search.text().strip() or None

        def _build() -> str:
            return self._service.build_preview_html(needle)

        # Dialogue masqué si la génération prend moins de 600 ms (rechargements silencieux)
        result = run_with_progress(
            self,
            "Liste des présents",
            "Préparation de l'aperçu HTML…",
            _build,
            minimum_duration=0 if confirm else 600,
        )
        if result is None:
            return
        # setHtml sur QWebEngineView = rendu Chromium asynchrone, ne bloque pas le thread UI
        self._web.setHtml(result, QUrl("about:blank"))
        self._loaded = True

    def _export(self) -> None:
        if not confirm_yes_no(
            self,
            "Export Excel",
            "Exporter la liste des présents vers un fichier Excel ?",
            default_no=True,
        ):
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la liste des présents",
            str(Path.home() / "liste_presents_sme.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not confirm_overwrite_file(self, path):
            return
        needle = self._search.text().strip() or None
        dest = Path(path)

        def _export() -> bool:
            self._service.export_excel(dest, search_filter=needle)
            return True

        if run_with_progress(self, "Export Excel", "Génération du classeur…", _export) is None:
            return
        QMessageBox.information(self, "Export", f"Fichier enregistré :\n{path}")
