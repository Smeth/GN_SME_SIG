"""Éditeur de mise en page : ordre des sections (glisser-déposer), titres, options."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService


class LayoutEditorView(QWidget):
    """Configuration dynamique des sections de la liste des présents."""

    def __init__(self, layout_service: LayoutConfigService, parent=None) -> None:
        super().__init__(parent)
        self._svc = layout_service
        self._cfg: ListeLayoutConfig = self._svc.load()

        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_select)

        self._code = QLineEdit()
        self._code.setReadOnly(True)
        self._title_main = QLineEdit()
        self._title_main.setPlaceholderText("Vide = désignation TabStructure (ligne principale)")
        self._title_sub = QLineEdit()
        self._title_sub.setPlaceholderText("Vide = code structure (sous-titre)")
        self._grp_sub = QCheckBox("Regrouper par subdivision d'arme")
        self._grp_sub.setChecked(True)
        self._grp_sub.stateChanged.connect(lambda _: self._update_sub_order_widget_state())

        self._sub_order = QPlainTextEdit()
        self._sub_order.setPlaceholderText(
            "Une ligne = un libellé de subdivision (exactement comme en base). "
            "Vide = ordre alphabétique pour cette structure."
        )
        self._sub_order.setMaximumHeight(110)
        self._sub_order.setTabChangesFocus(True)

        form = QFormLayout()
        form.addRow("Structure (code) :", self._code)
        form.addRow("Titre principal (override) :", self._title_main)
        form.addRow("Sous-titre (override) :", self._title_sub)
        form.addRow(self._grp_sub)
        form.addRow("Ordre des subdivisions (cette structure) :", self._sub_order)

        grp = QGroupBox("Section sélectionnée")
        grp.setLayout(form)

        btn_save = QPushButton("Enregistrer le fichier JSON")
        btn_save.clicked.connect(self._save_file)
        btn_reload = QPushButton("Recharger depuis le disque")
        btn_reload.clicked.connect(self._reload)
        btn_reset = QPushButton("Régénérer depuis TabStructure")
        btn_reset.clicked.connect(self._reset_db)
        btn_merge = QPushButton("Ajouter structures manquantes")
        btn_merge.clicked.connect(self._merge_missing)
        btn_remove = QPushButton("Supprimer la section")
        btn_remove.clicked.connect(self._remove_section)
        btn_apply = QPushButton("Appliquer les champs à la section")
        btn_apply.clicked.connect(self._apply_fields)

        bar = QHBoxLayout()
        for b in (btn_save, btn_reload, btn_reset, btn_merge, btn_apply, btn_remove):
            bar.addWidget(b)

        self._hint = QLabel(
            f"Fichier : <code>{self._svc.path()}</code><br/>"
            "Glissez-déposez les lignes pour l'ordre des <b>structures</b>. "
            "Pour chaque section : optionnellement, listez l'ordre des <b>subdivisions d'arme</b> "
            "(libellés tels qu'en base) ; les subdivisions non listées sont ajoutées à la fin "
            "(tri alphabétique, « (Sans subdivision) » en dernier)."
        )
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addWidget(QLabel("<b>Sections (ordre = ordre du document)</b>"))
        lay.addWidget(self._list, stretch=1)
        lay.addWidget(grp)
        lay.addLayout(bar)

        self._populate_list()
        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_sub_order_widget_state()

    def _update_sub_order_widget_state(self) -> None:
        sec = self._current_section()
        self._sub_order.setEnabled(bool(sec and self._grp_sub.isChecked()))

    def _populate_list(self) -> None:
        self._list.clear()
        for sec in sorted(self._cfg.sections, key=lambda s: s.order):
            label = f"{sec.order + 1:02d} — {sec.structure_code}"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, sec.id)
            self._list.addItem(it)

    def _current_section(self) -> SectionBlock | None:
        item = self._list.currentItem()
        if not item:
            return None
        sid = item.data(Qt.ItemDataRole.UserRole)
        for s in self._cfg.sections:
            if s.id == sid:
                return s
        return None

    def _on_select(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        self._code.setText(sec.structure_code)
        self._title_main.setText(sec.title_main_override or "")
        self._title_sub.setText(sec.title_sub_override or "")
        self._grp_sub.setChecked(sec.group_by_subdivision)
        so = sec.subdivision_order
        self._sub_order.setPlainText("\n".join(so) if so else "")
        self._update_sub_order_widget_state()

    def _apply_fields(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        tm = self._title_main.text().strip()
        ts = self._title_sub.text().strip()
        sec.title_main_override = tm or None
        sec.title_sub_override = ts or None
        sec.group_by_subdivision = self._grp_sub.isChecked()
        if self._grp_sub.isChecked():
            lines = [ln.strip() for ln in self._sub_order.toPlainText().splitlines()]
            lines = [ln for ln in lines if ln]
            sec.subdivision_order = lines if lines else None
        QMessageBox.information(self, "Section", "Modifications appliquées (pensez à Enregistrer).")

    def _reorder_sections_from_ui(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            sec = next(s for s in self._cfg.sections if s.id == sid)
            sec.order = i

    def _save_file(self) -> None:
        self._reorder_sections_from_ui()
        try:
            self._svc.save(self._cfg)
        except OSError as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        self._populate_list()
        QMessageBox.information(self, "OK", "Configuration enregistrée.")

    def _reload(self) -> None:
        self._cfg = self._svc.load()
        self._populate_list()

    def _reset_db(self) -> None:
        self._cfg = self._svc.build_default_from_database()
        self._populate_list()

    def _merge_missing(self) -> None:
        self._reorder_sections_from_ui()
        self._cfg = self._svc.merge_missing_structures(self._cfg)
        self._populate_list()

    def _remove_section(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        self._cfg.sections = [s for s in self._cfg.sections if s.id != sec.id]
        for i, s in enumerate(sorted(self._cfg.sections, key=lambda x: x.order)):
            s.order = i
        self._populate_list()
        if self._list.count():
            self._list.setCurrentRow(0)
