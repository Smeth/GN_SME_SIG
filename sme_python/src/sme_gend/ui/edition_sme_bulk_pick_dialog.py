"""Sélection structure / subdivision pour actions groupées (Édition SME)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.structure_combo import fill_structure_combo
from sme_gend.ui.structure_edit_dialog import StructureEditDialog


def _structure_id(settings: Settings, code: str) -> int | None:
    from sme_gend.repositories.mysql_coherence_repository import MySQLCoherenceRepository

    return MySQLCoherenceRepository(settings).structure_id_for_code(code)


def _ensure_subdivision_on_structure(
    settings: Settings, structure_code: str, libelle: str
) -> None:
    lib = (libelle or "").strip()
    sc = (structure_code or "").strip()
    if not sc or not lib:
        raise ValueError("Structure et libellé de subdivision requis.")
    ref = ReferentielService(settings)
    for x in ref.subdivision_arme_choices_for_structure(sc):
        if x.casefold() == lib.casefold():
            return
    sid = _structure_id(settings, sc)
    if sid is None:
        raise ValueError(f"Structure « {sc} » introuvable dans le référentiel.")
    from sme_gend.services.structure_organigram_service import StructureOrganigramService

    StructureOrganigramService(settings).add_subdivision(sid, lib)


class BulkStructurePickDialog(QDialog):
    """Choisir une structure du référentiel ou en créer une."""

    def __init__(
        self,
        parent: QWidget | None,
        settings: Settings,
        *,
        title: str,
        hint: str,
        initial_code: str | None = None,
        extra_codes: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._ref = ReferentielService(settings)
        self._picked_code: str | None = None

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setObjectName("EditionSmeBulkPickDialog")

        intro = QLabel(hint)
        intro.setWordWrap(True)

        self._structure = QComboBox()
        self._structure.setMinimumWidth(320)
        self._reload_structures(initial_code, extra_codes)

        btn_new = QPushButton("Nouvelle structure…")
        btn_new.clicked.connect(self._on_new_structure)

        struct_row = QHBoxLayout()
        struct_row.addWidget(self._structure, stretch=1)
        struct_row.addWidget(btn_new)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Valider")
        cancel_btn = bb.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Annuler")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(struct_row)
        self._reference_note = QLineEdit()
        self._reference_note.setPlaceholderText('Référence de la note — facultative')
        note_form = QFormLayout()
        note_form.addRow('Réf. note (facultative) :', self._reference_note)
        lay.addLayout(note_form)
        lay.addWidget(bb)

    def _reload_structures(
        self, initial_code: str | None, extra_codes: list[str] | None
    ) -> None:
        try:
            rows = self._ref.structures()
        except Exception as exc:
            QMessageBox.critical(self, "Structures", str(exc))
            rows = []
        fill_structure_combo(
            self._structure,
            rows,
            initial_code,
            include_empty=False,
            extra_codes=extra_codes,
        )

    def _on_new_structure(self) -> None:
        dlg = StructureEditDialog(self, None)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, designation, ordre, famille = dlg.values()
        if not code or not designation:
            QMessageBox.warning(self, "Structure", "Code et désignation sont obligatoires.")
            return
        if not confirm_yes_no(
            self,
            "Créer la structure",
            f"Créer « {code} » dans le référentiel MySQL ?",
            default_no=True,
        ):
            return
        try:
            self._ref.structure_insert(code, designation, ordre, famille_unite=famille)
        except Exception as exc:
            QMessageBox.critical(self, "Structure", str(exc))
            return
        self._reload_structures(code, [code])

    def _on_accept(self) -> None:
        code = (self._structure.currentData() or "").strip()
        if not code:
            code = self._structure.currentText().strip()
        if not code:
            QMessageBox.warning(self, "Structure", "Choisissez une structure.")
            return
        self._picked_code = code
        self.accept()

    def structure_code(self) -> str | None:
        return self._picked_code


class BulkSubdivisionPickDialog(QDialog):
    """Structure puis subdivision d'arme (référentiel + création)."""

    def __init__(
        self,
        parent: QWidget | None,
        settings: Settings,
        *,
        title: str,
        hint: str,
        initial_structure: str | None = None,
        extra_structure_codes: list[str] | None = None,
        initial_subdivision: str | None = None,
        subdivisions_for_structure: Callable[[str], list[str]] | None = None,
        scope_mois_label: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._ref = ReferentielService(settings)
        self._subdivisions_for_structure = subdivisions_for_structure
        self._structure_code: str | None = None
        self._subdivision: str | None = None
        self._all_subs: list[str] = []

        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setObjectName("EditionSmeBulkPickDialog")

        scope = (scope_mois_label or "").strip()
        hint_full = hint
        if scope and subdivisions_for_structure is not None:
            hint_full += (
                f"<br><br>Liste des subdivisions : <b>mois SME affiché ({scope})</b> "
                "(fiches du tableau), pas l'historique carrière complet."
            )
        intro = QLabel(hint_full)
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._structure = QComboBox()
        self._structure.currentIndexChanged.connect(self._reload_subdivisions)
        try:
            struct_rows = self._ref.structures()
        except Exception:
            struct_rows = []
        fill_structure_combo(
            self._structure,
            struct_rows,
            initial_structure,
            include_empty=False,
            extra_codes=extra_structure_codes,
        )

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filtrer la liste…")
        self._filter.textChanged.connect(self._apply_sub_filter)

        self._sub_list = QListWidget()
        self._sub_list.setAlternatingRowColors(True)
        self._sub_list.setMaximumHeight(220)
        self._sub_list.itemDoubleClicked.connect(lambda _: self._on_accept())

        create_box = QGroupBox("Créer une subdivision pour cette structure")
        self._new_sub = QLineEdit()
        self._new_sub.setPlaceholderText("Libellé de la nouvelle subdivision d'arme")
        btn_create = QPushButton("Créer et sélectionner")
        btn_create.clicked.connect(self._on_create_subdivision)
        create_lay = QFormLayout(create_box)
        row = QHBoxLayout()
        row.addWidget(self._new_sub, stretch=1)
        row.addWidget(btn_create)
        create_lay.addRow(row)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Valider")
        cancel_btn = bb.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn is not None:
            cancel_btn.setText("Annuler")
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Structure :", self._structure)
        form.addRow("Filtrer :", self._filter)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(QLabel("Subdivision d'arme :"))
        lay.addWidget(self._sub_list)
        lay.addWidget(create_box)
        lay.addWidget(bb)

        self._reload_subdivisions()
        if initial_subdivision:
            self._select_subdivision(initial_subdivision)

    def _current_structure_code(self) -> str:
        code = (self._structure.currentData() or "").strip()
        if not code:
            code = self._structure.currentText().strip()
        return code

    def _reload_subdivisions(self) -> None:
        sc = self._current_structure_code()
        self._all_subs = []
        if sc:
            try:
                if self._subdivisions_for_structure is not None:
                    self._all_subs = list(self._subdivisions_for_structure(sc))
                    if not self._all_subs:
                        self._all_subs = list(
                            self._ref.subdivisions_arme_for_structure(sc)
                        )
                else:
                    self._all_subs = self._ref.subdivision_arme_choices_for_structure(
                        sc
                    )
            except Exception as exc:
                QMessageBox.warning(self, "Subdivisions", str(exc))
        self._all_subs.sort(key=str.casefold)
        self._apply_sub_filter(self._filter.text())

    def _apply_sub_filter(self, text: str) -> None:
        needle = (text or "").strip().casefold()
        self._sub_list.clear()
        if not self._current_structure_code():
            self._sub_list.addItem("(Choisissez une structure)")
            item = self._sub_list.item(0)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        shown = 0
        for sub in self._all_subs:
            if needle and needle not in sub.casefold():
                continue
            self._sub_list.addItem(sub)
            shown += 1
        if shown == 0:
            self._sub_list.addItem("(Aucune subdivision — créez-en une ci-dessous)")
            item = self._sub_list.item(0)
            if item is not None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)

    def _select_subdivision(self, label: str) -> None:
        target = (label or "").strip().casefold()
        if not target:
            return
        for i in range(self._sub_list.count()):
            it = self._sub_list.item(i)
            if it and it.text().casefold() == target:
                self._sub_list.setCurrentItem(it)
                return

    def _on_create_subdivision(self) -> None:
        sc = self._current_structure_code()
        lib = self._new_sub.text().strip()
        if not sc:
            QMessageBox.warning(self, "Subdivision", "Choisissez d'abord une structure.")
            return
        if not lib:
            QMessageBox.warning(self, "Subdivision", "Saisissez le libellé de la subdivision.")
            return
        try:
            _ensure_subdivision_on_structure(self._settings, sc, lib)
        except Exception as exc:
            QMessageBox.critical(self, "Subdivision", str(exc))
            return
        self._new_sub.clear()
        self._filter.clear()
        seen: set[str] = set()
        merged: list[str] = []
        for raw in self._all_subs:
            k = (raw or "").strip().casefold()
            if not k or k in seen:
                continue
            seen.add(k)
            merged.append(raw.strip())
        k_lib = lib.casefold()
        if k_lib not in seen:
            merged.append(lib)
        self._all_subs = sorted(merged, key=str.casefold)
        self._apply_sub_filter("")
        self._select_subdivision(lib)
        item = self._sub_list.currentItem()
        if item is not None:
            self._sub_list.scrollToItem(item)
        self._structure_code = sc
        self._subdivision = lib
        self.accept()

    def _on_accept(self) -> None:
        sc = self._current_structure_code()
        if not sc:
            QMessageBox.warning(self, "Subdivision", "Choisissez une structure.")
            return
        item = self._sub_list.currentItem()
        sub = (item.text() if item else "").strip()
        if not sub or sub.startswith("("):
            sub = self._new_sub.text().strip()
        if not sub:
            QMessageBox.warning(
                self,
                "Subdivision",
                "Sélectionnez une subdivision dans la liste ou créez-en une nouvelle.",
            )
            return
        self._structure_code = sc
        self._subdivision = sub
        self.accept()

    def values(self) -> tuple[str, str] | None:
        if not self._structure_code or not self._subdivision:
            return None
        return self._structure_code, self._subdivision


def run_bulk_structure_pick(
    parent: QWidget | None,
    settings: Settings,
    *,
    person_count: int,
    initial_code: str | None = None,
    extra_codes: list[str] | None = None,
    with_reference: bool = False,
) -> str | tuple[str, str] | None:
    dlg = BulkStructurePickDialog(
        parent,
        settings,
        title=f"Changer la structure — {person_count} personne(s)",
        hint=(
            "Choisissez la structure d'affectation dans le référentiel. "
            "Vous pouvez créer une nouvelle structure si elle n'existe pas encore."
        ),
        initial_code=initial_code,
        extra_codes=extra_codes,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    code = dlg.structure_code()
    return (code, dlg._reference_note.text().strip()) if with_reference and code else code


def run_bulk_subdivision_pick(
    parent: QWidget | None,
    settings: Settings,
    *,
    person_count: int,
    initial_structure: str | None = None,
    extra_structure_codes: list[str] | None = None,
    subdivisions_for_structure: Callable[[str], list[str]] | None = None,
    scope_mois_label: str | None = None,
) -> tuple[str, str] | None:
    dlg = BulkSubdivisionPickDialog(
        parent,
        settings,
        title=f"Changer la subdivision — {person_count} personne(s)",
        hint=(
            "1. Choisissez la <b>structure</b>.<br>"
            "2. Sélectionnez une subdivision ou créez-en une nouvelle pour cette structure."
        ),
        initial_structure=initial_structure,
        extra_structure_codes=extra_structure_codes,
        initial_subdivision=None,
        subdivisions_for_structure=subdivisions_for_structure,
        scope_mois_label=scope_mois_label,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()
