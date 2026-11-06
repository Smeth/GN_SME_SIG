"""Fonctions autorisées pour une structure (ref_fonction_structure)."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.models.structure import StructureRow
from sme_gend.services.referentiel_service import ReferentielService


class StructureFonctionsDialog(QDialog):
    """Coche les fonctions utilisables pour la structure sélectionnée."""

    def __init__(
        self,
        service: ReferentielService,
        structure: StructureRow,
        *,
        parent=None,
        read_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Fonctions — {structure.code}")
        self.setMinimumSize(480, 520)
        self._service = service
        self._structure = structure
        self._read_only = read_only

        intro = QLabel(
            f"Fonctions saisissables pour <b>{structure.code}</b> "
            f"— {structure.designation or structure.code}."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrer les fonctions…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._count = QLabel()
        self._count.setObjectName("settingsPathsHint")

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        if not read_only:
            self._list.itemChanged.connect(lambda *_: self._refresh_count())

        from sme_gend.models.fonction_ref import FonctionRefRow
        self._load_error=False
        try:
            self._links_snapshot=service.structure_links_snapshot(structure.code)
            functions=[FonctionRefRow.from_sequence(raw) for raw in self._links_snapshot.choices]
            ids={int(raw[1]) for raw in self._links_snapshot.links}
            linked={str(raw[1]).strip() for raw in self._links_snapshot.choices if int(raw[0]) in ids}
            if not self._links_snapshot.links:linked=set(self._links_snapshot.observed)
            owner=self._links_snapshot.owner[0]
            intro.setText(f"Fonctions saisissables pour <b>{owner[1]}</b> — {owner[2] or owner[1]}.")
        except Exception:
            self._links_snapshot=None;functions=[];linked=set();self._load_error=True
            intro.setText("Impossible de charger les rattachements. Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
        for fn in sorted(functions,key=lambda row:row.libelle.casefold()):
            lib = (fn.libelle or "").strip()
            if not lib:
                continue
            item = QListWidgetItem(lib)
            item.setData(Qt.ItemDataRole.UserRole, lib)
            flags = item.flags()
            if read_only:
                item.setFlags(flags & ~Qt.ItemFlag.ItemIsUserCheckable)
            else:
                item.setFlags(flags | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if lib in linked else Qt.CheckState.Unchecked
                )
            self._list.addItem(item)

        btn_all = QPushButton("Tout cocher")
        from sme_gend.ui.check_all import bind_list_check_all
        bind_list_check_all(btn_all, self._list, self._search)
        btn_none = QPushButton("Tout décocher")
        btn_none.clicked.connect(lambda: self._set_visible_checked(False))
        if read_only:
            btn_all.setEnabled(False)
            btn_none.setEnabled(False)

        sel_row = QHBoxLayout()
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        sel_row.addWidget(self._count)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        if read_only:
            bb.button(QDialogButtonBox.StandardButton.Save).hide()
        else:
            bb.button(QDialogButtonBox.StandardButton.Save).setText("Enregistrer")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Fermer" if read_only else "Annuler")
        bb.button(QDialogButtonBox.StandardButton.Save).setEnabled(not self._load_error and not read_only)
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self._search)
        lay.addWidget(self._list, stretch=1)
        lay.addLayout(sel_row)
        lay.addWidget(bb)
        self._refresh_count()

    def _apply_filter(self) -> None:
        q = normalize_search_text(self._search.text().strip().casefold())
        for i in range(self._list.count()):
            item = self._list.item(i)
            show = not q or q in normalize_search_text(item.text().casefold())
            item.setHidden(not show)

    def _set_visible_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not item.isHidden() and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)
        self._refresh_count()

    def _refresh_count(self) -> None:
        n = sum(
            1
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        )
        self._count.setText(f"{n} fonction(s) cochée(s)")

    def _on_save(self) -> None:
        if getattr(self,"_load_error",False):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self,"Rattachements indisponibles","Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
            return
        if self._read_only:
            self.reject()
            return
        selected: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(str(item.data(Qt.ItemDataRole.UserRole) or item.text()))
        try:
            self._service.fonction_set_links_for_structure(self._structure.code, selected, expected=self._links_snapshot)
        except ReferenceConflictError as exc:
            show_reference_conflict(self,exc)
            return
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        self.accept()
