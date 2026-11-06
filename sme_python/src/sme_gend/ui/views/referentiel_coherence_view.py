"""Harmonisation manuelle SubArme et structures (toutes archives MySQL)."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.repositories.mysql_coherence_repository import (
    StructureVariantRow,
    SubdivVariantRow,
)
from sme_gend.services.referentiel_coherence_service import ReferentielCoherenceService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.view_lifecycle import ViewDataState

_READ_ONLY = (
    Qt.ItemFlag.ItemIsSelectable
    | Qt.ItemFlag.ItemIsEnabled
)


def _readonly_cell(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(_READ_ONLY)
    return it


class ReferentielCoherenceView(QWidget):
    def __init__(self, referentiel: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._ref = referentiel
        self._svc = ReferentielCoherenceService(referentiel.settings)
        self._view_data = ViewDataState(stale=True)
        self._subdiv_rows: list[SubdivVariantRow] = []
        self._struct_rows: list[StructureVariantRow] = []
        self._sub_snapshot = None
        self._struct_snapshot = None

        intro = QLabel(
            "Choisissez vous-même les variantes à fusionner. Les modifications s'appliquent à "
            "<b>toutes</b> les situations mensuelles enregistrées (archives incluses)."
        )
        intro.setWordWrap(True)
        intro.setObjectName("dashSubtitle")

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_subdiv_tab(), "SubArme")
        self._tabs.addTab(self._build_structure_tab(), "Structures")

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self._tabs, stretch=1)

    def _build_subdiv_tab(self) -> QWidget:
        w = QWidget()
        self._sub_struct = QComboBox()
        self._sub_struct.setMinimumWidth(280)
        self._sub_struct.currentIndexChanged.connect(self._load_subdiv_table)

        self._sub_table = QTableWidget(0, 5)
        self._sub_table.setHorizontalHeaderLabels(
            ["", "SubArme (libellé)", "RSA id", "Segments carrière", "Référentiel"]
        )
        self._sub_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._sub_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._sub_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sub_table.setAlternatingRowColors(True)
        self._sub_table.cellDoubleClicked.connect(self._on_subdiv_cell_double_clicked)

        self._sub_target = QLineEdit()
        self._sub_target.setPlaceholderText("Libellé unique après fusion")

        self._btn_sub_preview = QPushButton("Aperçu…")
        self._btn_sub_apply = QPushButton("Appliquer la fusion")
        self._btn_sub_apply.setObjectName("primaryButton")
        self._btn_sub_preview.clicked.connect(self._preview_subdiv)
        self._btn_sub_apply.clicked.connect(self._apply_subdiv)

        form = QFormLayout()
        form.addRow("Structure", self._sub_struct)
        form.addRow("Libellé cible", self._sub_target)

        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(self._btn_sub_preview)
        bar.addWidget(self._btn_sub_apply)

        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self._sub_table, stretch=1)
        lay.addLayout(bar)
        return w

    def _build_structure_tab(self) -> QWidget:
        w = QWidget()
        self._struct_table = QTableWidget(0, 5)
        self._struct_table.setHorizontalHeaderLabels(
            ["", "Code", "Désignation", "Segments carrière", "Source"]
        )
        self._struct_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._struct_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._struct_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._struct_table.setAlternatingRowColors(True)
        self._struct_table.cellDoubleClicked.connect(self._on_struct_cell_double_clicked)

        self._struct_keeper = QComboBox()
        self._struct_keeper.setMinimumWidth(280)
        self._struct_keeper.currentIndexChanged.connect(self._sync_structure_target_fields)
        self._struct_code = QLineEdit()
        self._struct_code.setPlaceholderText("Code final (ex. DPF)")
        self._struct_desig = QLineEdit()
        self._struct_desig.setPlaceholderText("Désignation (optionnel)")

        self._btn_struct_reload = QPushButton("Actualiser")
        self._btn_struct_reload.clicked.connect(self._load_structure_table)
        self._btn_struct_preview = QPushButton("Aperçu…")
        self._btn_struct_apply = QPushButton("Fusionner vers la structure gardienne")
        self._btn_struct_apply.setObjectName("primaryButton")
        self._btn_struct_preview.clicked.connect(self._preview_structure)
        self._btn_struct_apply.clicked.connect(self._apply_structure)

        grp = QGroupBox("Structure gardienne et libellés finaux")
        gf = QFormLayout(grp)
        gf.addRow("Gardienne (référentiel)", self._struct_keeper)
        gf.addRow("Code cible", self._struct_code)
        gf.addRow("Désignation", self._struct_desig)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_struct_reload)
        bar.addStretch()
        bar.addWidget(self._btn_struct_preview)
        bar.addWidget(self._btn_struct_apply)

        lay = QVBoxLayout(w)
        lay.addWidget(self._struct_table, stretch=1)
        lay.addWidget(grp)
        lay.addLayout(bar)
        return w

    def on_page_activated(self, *, structure_code: str | None = None) -> None:
        if self._view_data.needs_load():
            if self._reload_struct_combo() and self._load_structure_table():
                self._view_data.finish_load()
            else:
                self._view_data.mark_stale()
        if structure_code:
            self.select_structure_code(structure_code)
        self._tabs.setCurrentIndex(0 if structure_code else self._tabs.currentIndex())

    def select_structure_code(self, code: str) -> None:
        c = (code or "").strip()
        if not c:
            return
        idx = self._sub_struct.findData(c)
        if idx >= 0:
            self._sub_struct.setCurrentIndex(idx)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def _reload_struct_combo(self) -> bool:
        self._sub_snapshot=None
        try:
            rows=run_reference_task(self,"Référentiel",lambda: self._ref.structures())
        except Exception as error:
            self._view_data.mark_stale()
            QMessageBox.critical(self,"Structures",str(error))
            return False
        selected=self._sub_struct.currentData()
        self._sub_struct.blockSignals(True)
        try:
            self._sub_struct.clear()
            for row in rows:self._sub_struct.addItem(f"{row.code} — {row.designation}",row.code)
            index=self._sub_struct.findData(selected)
            if index>=0:self._sub_struct.setCurrentIndex(index)
        finally:self._sub_struct.blockSignals(False)
        return self._load_subdiv_table()

    def _current_structure(self) -> tuple[int | None, str]:
        code = str(self._sub_struct.currentData() or "").strip()
        if not code:
            return None, ""
        from sme_gend.repositories.mysql_coherence_repository import coherence_repository

        sid = run_reference_task(self,"Référentiel",lambda: coherence_repository(self._ref.settings).structure_id_for_code(code))
        return sid, code

    def _load_subdiv_table(self) -> bool:
        self._sub_snapshot=None
        try:
            sid, code = self._current_structure()
        except Exception as error:
            QMessageBox.critical(self,"SubArme",str(error))
            return False
        self._subdiv_rows = []
        self._sub_snapshot = None
        self._sub_table.setRowCount(0)
        if sid is None or not code:
            return True
        try:
            self._subdiv_rows = run_reference_task(self,"Référentiel",lambda: self._svc.subdiv_variants(sid, code))
            self._sub_snapshot = getattr(self._subdiv_rows,"write_snapshot",None)
        except Exception as e:
            QMessageBox.critical(self, "SubArme", str(e))
            return False
        self._sub_table.setRowCount(len(self._subdiv_rows))
        for i, row in enumerate(self._subdiv_rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self._sub_table.setItem(i, 0, chk)
            self._sub_table.setItem(i, 1, _readonly_cell(row.libelle))
            self._sub_table.setItem(
                i, 2, _readonly_cell(str(row.rsa_id) if row.rsa_id else "—")
            )
            self._sub_table.setItem(i, 3, _readonly_cell(str(row.segment_count)))
            self._sub_table.setItem(
                i, 4, _readonly_cell("Oui" if row.in_referentiel else "Non")
            )
        return True

    def _load_structure_table(self) -> bool:
        self._struct_rows = []
        self._struct_snapshot = None
        self._struct_table.setRowCount(0)
        self._struct_keeper.blockSignals(True)
        self._struct_keeper.clear()
        try:
            self._struct_rows = run_reference_task(self,"Référentiel",lambda: self._svc.structure_variants())
            self._struct_snapshot = getattr(self._struct_rows,"write_snapshot",None)
        except Exception as e:
            QMessageBox.critical(self, "Structures", str(e))
            self._struct_keeper.blockSignals(False)
            return False
        self._struct_table.setRowCount(len(self._struct_rows))
        for i, row in enumerate(self._struct_rows):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Unchecked)
            self._struct_table.setItem(i, 0, chk)
            self._struct_table.setItem(i, 1, _readonly_cell(row.code))
            self._struct_table.setItem(i, 2, _readonly_cell(row.designation or "—"))
            self._struct_table.setItem(i, 3, _readonly_cell(str(row.segment_count)))
            src = "Référentiel" if row.source == "referentiel" else "Texte carrière seul"
            self._struct_table.setItem(i, 4, _readonly_cell(src))
            if row.structure_id is not None:
                self._struct_keeper.addItem(
                    f"{row.code} — {row.designation or ''}", int(row.structure_id)
                )
        self._struct_keeper.blockSignals(False)
        if self._struct_keeper.count():
            self._struct_keeper.setCurrentIndex(0)
            self._sync_structure_target_fields()
        return True

    def _sync_structure_target_fields(self) -> None:
        kid = self._struct_keeper.currentData()
        if kid is None:
            return
        for row in self._struct_rows:
            if row.structure_id == int(kid):
                self._struct_code.setText(row.code)
                if row.designation:
                    self._struct_desig.setText(row.designation)
                break

    def _on_subdiv_cell_double_clicked(self, row: int, column: int) -> None:
        if column == 1 and 0 <= row < len(self._subdiv_rows):
            self._sub_target.setText(self._subdiv_rows[row].libelle)
            self._sub_target.setFocus()
            self._sub_target.selectAll()

    def _on_struct_cell_double_clicked(self, row: int, column: int) -> None:
        if row < 0 or row >= len(self._struct_rows):
            return
        r = self._struct_rows[row]
        if column in (1, 2):
            self._struct_code.setText(r.code)
            if r.designation:
                self._struct_desig.setText(r.designation)

    def _checked_subdiv_indices(self) -> list[int]:
        out: list[int] = []
        for i in range(self._sub_table.rowCount()):
            it = self._sub_table.item(i, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                out.append(i)
        return out

    def _checked_struct_indices(self) -> list[int]:
        out: list[int] = []
        for i in range(self._struct_table.rowCount()):
            it = self._struct_table.item(i, 0)
            if it and it.checkState() == Qt.CheckState.Checked:
                out.append(i)
        return out

    def _ensure_editable(self) -> bool:
        from sme_gend.repositories.mysql_connection import mysql_database_key
        if mysql_database_key(self._svc._settings)!=mysql_database_key(self._ref.settings):
            QMessageBox.warning(self,"Harmonisation","Connexion changee. Fermez et rechargez cette fenetre avant de modifier.")
            return False
        st = evaluate_editing_state(self._ref.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail)
            return False
        return True

    def _preview_subdiv(self) -> None:
        sid, code = self._current_structure()
        if sid is None:
            QMessageBox.information(self, "SubArme", "Sélectionnez une structure.")
            return
        sel = self._checked_subdiv_indices()
        if len(sel) < 2:
            QMessageBox.information(
                self,
                "SubArme",
                "Cochez au moins deux lignes à fusionner.",
            )
            return
        target = self._sub_target.text().strip()
        if not target:
            QMessageBox.information(self, "SubArme", "Indiquez le libellé cible.")
            return
        merge_rsa: list[int] = []
        merge_labels: list[str] = []
        keeper: int | None = None
        for i in sel:
            r = self._subdiv_rows[i]
            merge_labels.append(r.libelle)
            if r.rsa_id:
                merge_rsa.append(int(r.rsa_id))
                if keeper is None:
                    keeper = int(r.rsa_id)
        try:
            prev = run_reference_task(self,"Référentiel",lambda: self._svc.preview_subdiv_merge(
                sid,
                code,
                keeper_rsa_id=keeper,
                target_libelle=target,
                merge_rsa_ids=merge_rsa,
                merge_libelles=merge_labels,
            ))
        except Exception as e:
            QMessageBox.critical(self, "Aperçu", str(e))
            return
        QMessageBox.information(
            self,
            "Aperçu fusion SubArme",
            f"Libellés fusionnés : {len(merge_labels)}\n"
            f"RSA fusionnés : {prev.rsa_merged}\n"
            f"Segments carrière mis à jour (estimation) : {prev.carriere_rows}\n"
            f"Libellé final : « {target} »",
        )

    def _apply_subdiv(self) -> None:
        expected = self._sub_snapshot
        if expected is None:
            QMessageBox.warning(self,"Harmonisation","Version indisponible. Actualisez les donnees avant de reprendre.")
            return
        if not self._ensure_editable():
            return
        sid, code = self._current_structure()
        if sid is None:
            return
        sel = self._checked_subdiv_indices()
        if len(sel) < 2:
            QMessageBox.information(self, "SubArme", "Cochez au moins deux lignes.")
            return
        target = self._sub_target.text().strip()
        if not target:
            QMessageBox.information(self, "SubArme", "Libellé cible requis.")
            return
        merge_rsa: list[int] = []
        merge_labels: list[str] = []
        keeper: int | None = None
        for i in sel:
            r = self._subdiv_rows[i]
            merge_labels.append(r.libelle)
            if r.rsa_id:
                merge_rsa.append(int(r.rsa_id))
                if keeper is None:
                    keeper = int(r.rsa_id)
        if not confirm_yes_no(
            self,
            "Confirmer la fusion SubArme",
            f"Fusionner {len(merge_labels)} variante(s) sous « {target} » "
            f"pour la structure {code}, sur toutes les archives ?",
            default_no=True,
        ):
            return
        try:
            res = run_reference_task(self,"Référentiel",lambda: self._svc.apply_subdiv_merge(
                sid,
                code,
                keeper_rsa_id=keeper,
                target_libelle=target,
                merge_rsa_ids=merge_rsa,
                merge_libelles=merge_labels,
                expected=expected,
            ))
        except Exception as e:
            QMessageBox.critical(self, "Fusion SubArme", str(e))
            return
        self._load_subdiv_table()
        QMessageBox.information(
            self,
            "Fusion terminée",
            f"Segments carrière mis à jour : {res.carriere_updated}\n"
            f"RSA fusionnés : {res.rsa_merged}",
        )

    def _preview_structure(self) -> None:
        kid = self._struct_keeper.currentData()
        if kid is None:
            QMessageBox.information(self, "Structures", "Choisissez une structure gardienne.")
            return
        keeper_code = self._struct_code.text().strip()
        sel = self._checked_struct_indices()
        absorb_ids: list[int] = []
        absorb_text: list[str] = []
        for i in sel:
            r = self._struct_rows[i]
            if r.structure_id is not None and int(r.structure_id) != int(kid):
                absorb_ids.append(int(r.structure_id))
            elif r.structure_id is None:
                absorb_text.append(r.code)
        if not absorb_ids and not absorb_text:
            QMessageBox.information(
                self,
                "Structures",
                "Cochez au moins une autre variante à absorber (pas la gardienne).",
            )
            return
        try:
            prev = run_reference_task(self,"Référentiel",lambda: self._svc.preview_structure_merge(
                keeper_structure_id=int(kid),
                keeper_code=keeper_code,
                absorb_structure_ids=absorb_ids,
                absorb_text_codes=absorb_text,
            ))
        except Exception as e:
            QMessageBox.critical(self, "Aperçu", str(e))
            return
        QMessageBox.information(
            self,
            "Aperçu fusion structures",
            f"Segments carrière : {prev.carriere_rows}\n"
            f"RSA à rattacher / fusionner : {prev.rsa_moved}\n"
            f"Entrées référentiel supprimées : {prev.structures_removed}\n"
            f"Code cible : {self._struct_code.text().strip()}",
        )

    def _apply_structure(self) -> None:
        target_designation=self._struct_desig.text().strip() or None
        expected = self._struct_snapshot
        if expected is None:
            QMessageBox.warning(self,"Harmonisation","Version indisponible. Actualisez les donnees avant de reprendre.")
            return
        if not self._ensure_editable():
            return
        kid = self._struct_keeper.currentData()
        if kid is None:
            return
        target_code = self._struct_code.text().strip()
        if not target_code:
            QMessageBox.information(self, "Structures", "Code cible requis.")
            return
        keeper_code = ""
        for row in self._struct_rows:
            if row.structure_id == int(kid):
                keeper_code = row.code
                break
        sel = self._checked_struct_indices()
        absorb_ids: list[int] = []
        absorb_codes: list[str] = []
        absorb_text: list[str] = []
        for i in sel:
            r = self._struct_rows[i]
            if r.structure_id is not None and int(r.structure_id) != int(kid):
                absorb_ids.append(int(r.structure_id))
                absorb_codes.append(r.code)
            elif r.structure_id is None:
                absorb_text.append(r.code)
        if not absorb_ids and not absorb_text:
            QMessageBox.information(self, "Structures", "Cochez les variantes à fusionner.")
            return
        if not confirm_yes_no(
            self,
            "Confirmer fusion structures",
            f"Absorber {len(absorb_ids) + len(absorb_text)} variante(s) "
            f"vers « {target_code} » (toutes archives) ?",
            default_no=True,
        ):
            return
        try:
            res = run_reference_task(self,"Référentiel",lambda: self._svc.apply_structure_merge(
                keeper_structure_id=int(kid),
                keeper_code=keeper_code or target_code,
                target_code=target_code,
                target_designation=target_designation,
                absorb_structure_ids=absorb_ids,
                absorb_structure_codes=absorb_codes,
                absorb_text_codes=absorb_text,
                expected=expected,
            ))
        except Exception as e:
            QMessageBox.critical(self, "Fusion structures", str(e))
            return
        self.mark_dirty()
        self._reload_struct_combo()
        self._load_structure_table()
        QMessageBox.information(
            self,
            "Fusion terminée",
            f"Segments carrière : {res.carriere_updated}\n"
            f"RSA rattachés : {res.rsa_relinked}\n"
            f"Structures supprimées du référentiel : {res.structures_removed}",
        )
