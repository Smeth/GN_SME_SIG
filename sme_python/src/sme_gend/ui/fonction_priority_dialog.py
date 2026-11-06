"""Configuration de l'ordre prioritaire des fonctions (liste générale)."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.ui.shared_config_editor import displayed_config, config_editor_action
from sme_gend.config.settings import Settings
from sme_gend.models.fonction_priority_config import FonctionPriorityConfig
from sme_gend.services.fonction_priority_service import (
    bootstrap_default_config,
    ensure_fonction_priority_config,
    save_fonction_priority_config,
)
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class FonctionPriorityDialog(QDialog):
    """Ordre des fonctions en tête de la liste générale (ref_fonction.id)."""

    def __init__(
        self,
        settings: Settings,
        referentiel: ReferentielService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._referentiel = referentiel
        self.setWindowTitle("Ordre des fonctions")
        self.setMinimumSize(560, 420)

        self._hint = QLabel("Ces fonctions s’affichent en tête de la liste Effectifs.")
        self._hint.setWordWrap(True)

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        self._combo = QComboBox()
        self._combo.setMinimumWidth(280)

        btn_add = QPushButton("Ajouter →")
        btn_add.clicked.connect(self._add_selected)
        btn_remove = QPushButton("Retirer")
        btn_remove.clicked.connect(self._remove_selected)
        btn_up = QPushButton("Monter")
        btn_up.clicked.connect(self._move_up)
        btn_down = QPushButton("Descendre")
        btn_down.clicked.connect(self._move_down)
        btn_default = QPushButton("Ordre habituel")
        btn_default.setToolTip("Remet COMGEND, C2 CEMGEND, etc. en tête.")
        btn_default.clicked.connect(self._restore_legacy)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Ajouter :"))
        add_row.addWidget(self._combo, stretch=1)
        add_row.addWidget(btn_add)

        btn_col = QVBoxLayout()
        btn_col.addWidget(btn_up)
        btn_col.addWidget(btn_down)
        btn_col.addWidget(btn_remove)
        btn_col.addStretch()
        btn_col.addWidget(btn_default)
        reload_btn = QPushButton("Recharger")
        reload_btn.clicked.connect(self._reload_confirmed)
        btn_col.addWidget(reload_btn)

        mid = QHBoxLayout()
        mid.addWidget(self._list, stretch=1)
        mid.addLayout(btn_col)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("Enregistrer")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("Annuler")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(self._hint)
        root.addLayout(add_row)
        root.addLayout(mid, stretch=1)
        root.addWidget(buttons)

        self._all_fonctions: dict[int, str] = {}
        self._reload()

    def _reload_confirmed(self) -> None:
        if confirm_yes_no(self,"Recharger","Les modifications non enregistrées seront perdues. Recharger ?",default_no=True):
            self._reload()

    @config_editor_action
    def _reload(self) -> None:
        try:
            rows = run_reference_task(self,"Référentiel",lambda: self._referentiel.fonctions_all())
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            return
        cfg = run_reference_task(self,"Référentiel",lambda: ensure_fonction_priority_config(self._settings))
        self._loaded_config = cfg
        self._all_fonctions = {r.id: r.libelle for r in rows}
        self._list.clear()
        for fid in cfg.fonction_ids:
            label = self._all_fonctions.get(fid)
            if not label:
                continue
            self._append_item(fid, label)
        self._refresh_combo()

    def _append_item(self, fid: int, label: str) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, int(fid))
        self._list.addItem(item)

    def _refresh_combo(self) -> None:
        used = {
            self._list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._list.count())
        }
        self._combo.blockSignals(True)
        self._combo.clear()
        for fid, label in sorted(self._all_fonctions.items(), key=lambda x: x[1].casefold()):
            if fid in used:
                continue
            self._combo.addItem(label, fid)
        self._combo.blockSignals(False)

    def _add_selected(self) -> None:
        fid = self._combo.currentData()
        if fid is None:
            return
        label = self._all_fonctions.get(int(fid), "")
        self._append_item(int(fid), label)
        self._refresh_combo()

    def _remove_selected(self) -> None:
        row = self._list.currentRow()
        if row >= 0:
            self._list.takeItem(row)
            self._refresh_combo()

    def _move_up(self) -> None:
        row = self._list.currentRow()
        if row <= 0:
            return
        item = self._list.takeItem(row)
        self._list.insertItem(row - 1, item)
        self._list.setCurrentRow(row - 1)

    def _move_down(self) -> None:
        row = self._list.currentRow()
        if row < 0 or row >= self._list.count() - 1:
            return
        item = self._list.takeItem(row)
        self._list.insertItem(row + 1, item)
        self._list.setCurrentRow(row + 1)

    def _restore_legacy(self) -> None:
        if not confirm_yes_no(
            self,
            "Ordre habituel",
            "Remplacer par l’ordre habituel (COMGEND, C2 CEMGEND, …) ?",
            default_no=True,
        ):
            return
        cfg = bootstrap_default_config(self._settings)
        if not cfg.fonction_ids:
            QMessageBox.warning(
                self,
                "Ordre habituel",
                "Aucune fonction correspondante dans le catalogue.",
            )
            return
        self._list.clear()
        for fid in cfg.fonction_ids:
            label = self._all_fonctions.get(fid, f"Fonction #{fid}")
            self._append_item(fid, label)
        self._refresh_combo()

    def _collect_config(self) -> FonctionPriorityConfig:
        ids: list[int] = []
        for i in range(self._list.count()):
            fid = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if fid is not None:
                ids.append(int(fid))
        cfg = displayed_config(self)
        cfg.fonction_ids = ids
        return cfg

    @config_editor_action
    def _save(self) -> None:
        cfg = self._collect_config()
        run_reference_task(self,"Référentiel",lambda: save_fonction_priority_config(self._settings, cfg))
        self._loaded_config = cfg
        self.accept()
