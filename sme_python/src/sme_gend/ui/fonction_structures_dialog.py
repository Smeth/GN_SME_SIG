"""Dialogue — rattacher une fonction aux structures autorisées."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

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

from sme_gend.services.referentiel_service import ReferentielService


class FonctionStructuresDialog(QDialog):
    """Coche les structures où la fonction est utilisable."""

    def __init__(
        self,
        service: ReferentielService,
        fonction_id: int,
        fonction_libelle: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Structures — {fonction_libelle}")
        self.setMinimumSize(460, 520)
        self._service = service
        self._fonction_id = fonction_id

        intro = QLabel(f"Structures où <b>{fonction_libelle}</b> peut être saisie.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrer les structures…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._count = QLabel()
        self._count.setObjectName("settingsPathsHint")

        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.itemChanged.connect(lambda *_: self._refresh_count())

        from sme_gend.models.structure import StructureRow
        self._load_error=False
        try:
            self._links_snapshot=run_reference_task(self,"Référentiel",lambda: service.fonction_links_snapshot(fonction_id))
            structures=[StructureRow.from_sequence(raw[1:]) for raw in self._links_snapshot.choices]
            linked_ids={int(raw[0]) for raw in self._links_snapshot.links}
            linked={str(raw[1]).strip() for raw in self._links_snapshot.choices if int(raw[0]) in linked_ids}
            fonction_libelle=str(self._links_snapshot.owner[0][1] or "").strip()
            self.setWindowTitle(f"Structures — {fonction_libelle}")
        except Exception:
            self._links_snapshot=None;structures=[];linked=set();self._load_error=True
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

        intro.setText(
            f"Structures où <b>{fonction_libelle}</b> peut être saisie.<br>"
            "Le contrôle spécial et les détachés n'apparaissent pas : "
            "leurs fonctions ne sont pas rattachées."
        )
        if self._load_error:
            intro.setText("Impossible de charger les rattachements. Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
        for st in sorted(structures,key=lambda row:((row.ordre_tri is not None),row.ordre_tri or 0,row.code)):
            if structure_fonctions_non_liees(st.code) or structure_fonctions_non_liees(
                st.designation
            ):
                continue
            item = QListWidgetItem(f"{st.code} — {st.designation or st.code}")
            item.setData(Qt.ItemDataRole.UserRole, st.code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if st.code in linked else Qt.CheckState.Unchecked
            )
            self._list.addItem(item)

        btn_all = QPushButton("Tout cocher")
        from sme_gend.ui.check_all import bind_list_check_all
        bind_list_check_all(btn_all, self._list, self._search)
        btn_none = QPushButton("Tout décocher")
        btn_none.clicked.connect(lambda: self._set_visible_checked(False))
        sel_row = QHBoxLayout()
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch()
        sel_row.addWidget(self._count)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)
        save_btn = bb.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText("Enregistrer")
            save_btn.setEnabled(not self._load_error)
        cancel_btn = bb.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("Annuler")

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(intro)
        lay.addWidget(self._search)
        lay.addLayout(sel_row)
        lay.addWidget(self._list, stretch=1)
        lay.addWidget(bb)
        self._refresh_count()

    def _visible_items(self) -> list[QListWidgetItem]:
        return [
            self._list.item(i)
            for i in range(self._list.count())
            if not self._list.isRowHidden(i)
        ]

    def _apply_filter(self, text: str) -> None:
        needle = normalize_search_text((text or "").strip().casefold())
        for i in range(self._list.count()):
            item = self._list.item(i)
            blob = normalize_search_text((item.text() if item else "").casefold())
            self._list.setRowHidden(i, bool(needle) and needle not in normalize_search_text(blob))
        self._refresh_count()

    def _set_visible_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._list.blockSignals(True)
        for item in self._visible_items():
            item.setCheckState(state)
        self._list.blockSignals(False)
        self._refresh_count()

    def _refresh_count(self) -> None:
        checked = 0
        total = self._list.count()
        for i in range(total):
            item = self._list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                checked += 1
        self._count.setText(f"{checked} / {total} structure(s)")

    def _on_save(self) -> None:
        if getattr(self,"_load_error",False):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self,"Rattachements indisponibles","Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
            return
        codes: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.checkState() == Qt.CheckState.Checked:
                code = item.data(Qt.ItemDataRole.UserRole)
                if code:
                    codes.append(str(code))
        try:
            run_reference_task(self,"Référentiel",lambda: self._service.fonction_set_structure_links(self._fonction_id, codes, expected=self._links_snapshot))
        except ReferenceConflictError as exc:
            show_reference_conflict(self,exc)
            return
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(self, "Enregistrement", str(exc))
            return
        self.accept()
