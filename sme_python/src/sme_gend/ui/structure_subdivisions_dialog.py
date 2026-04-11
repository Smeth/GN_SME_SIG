"""Fenêtre modale : subdivisions d'arme (SubArme) liées à une structure dans ListeGenerale."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.models.structure import StructureRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class StructureSubdivisionsDialog(QDialog):
    """Liste des SubArme distincts pour une structure + accès rapide à la modification de la structure."""

    def __init__(
        self,
        parent,
        service: ReferentielService,
        structure: StructureRow,
        *,
        on_structure_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Subdivisions d'arme — {structure.code}")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setObjectName("StructureSubdivisionsDialog")

        self._service = service
        self._structure = structure
        self._on_structure_changed = on_structure_changed

        title = QLabel(structure.designation or structure.code)
        title.setObjectName("modalTitle")
        title.setWordWrap(True)
        tf = QFont()
        tf.setPointSize(13)
        tf.setWeight(QFont.Weight.DemiBold)
        title.setFont(tf)

        sub = QLabel(
            f"Code structure : <b>{structure.code}</b><br/>"
            "Subdivisions d'arme présentes dans <b>ListeGenerale</b> (champ SubArme) "
            "pour ce code de structure."
        )
        sub.setWordWrap(True)
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setObjectName("modalSubtitle")

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setMinimumHeight(200)

        self._btn_refresh = QPushButton("Actualiser la liste")
        self._btn_refresh.clicked.connect(lambda: self._reload_list(silent=False))
        self._btn_edit_struct = QPushButton("Modifier la structure…")
        self._btn_edit_struct.clicked.connect(self._open_edit_structure)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_refresh)
        btn_row.addWidget(self._btn_edit_struct)
        btn_row.addStretch()

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Fermer")

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addLayout(btn_row)
        lay.addWidget(self._list, stretch=1)
        lay.addWidget(bb)

        self._reload_list(silent=True)

    def _reload_list(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser",
                "Recharger la liste des subdivisions depuis la base Access ?",
                default_no=False,
            ):
                return
        self._list.clear()
        try:
            subs = self._service.subdivisions_arme_for_structure(self._structure.code)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Subdivisions", str(e))
            return
        if not subs:
            self._list.addItem("(Aucune subdivision d'arme enregistrée pour cette structure)")
            i = self._list.item(0)
            if i:
                i.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        for s in subs:
            self._list.addItem(s)

    def _open_edit_structure(self) -> None:
        from sme_gend.ui.views.referentiel_views import _StructureDialog

        dlg = _StructureDialog(self, self._structure)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, designation, ordre = dlg.values()
        if not designation:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Structure", "La désignation est obligatoire.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Enregistrer les modifications de la structure « {code} » dans TabStructure (Access) ?",
            default_no=True,
        ):
            return
        try:
            self._service.structure_update(code, designation, ordre)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._structure = StructureRow(code=code, designation=designation, ordre_tri=ordre)
        self.setWindowTitle(f"Subdivisions d'arme — {code}")
        self._reload_list(silent=True)
        if self._on_structure_changed is not None:
            self._on_structure_changed()
