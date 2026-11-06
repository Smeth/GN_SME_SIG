"""Choix des colonnes pour l'état filtré de la liste générale."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.liste_generale_print import PRINT_COLUMNS


class ListeGeneraleEtatDialog(QDialog):
    """Colonnes de l'état, puis Excel, PDF ou impression."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        row_count: int,
        mois_label: str,
        visible_keys: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle("Exporter / imprimer l'état")
        self.setMinimumWidth(640)
        self._action = ""
        self._boxes: dict[str, QCheckBox] = {}
        self._visible_keys = list(visible_keys or [])

        intro = QLabel(
            f"<b>{row_count}</b> agent(s) selon les filtres et la recherche en cours."
            + (f"<br/>Période : {mois_label}." if mois_label else "")
        )
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setWordWrap(True)

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        for index, (key, label, _width) in enumerate(PRINT_COLUMNS):
            box = QCheckBox(label)
            box.setChecked(True)
            self._boxes[key] = box
            grid.addWidget(box, index // 2, index % 2)
        columns = QGroupBox("Colonnes de l'état")
        columns.setLayout(grid)

        btn_all = self._action_button("Tout cocher")
        btn_none = self._action_button("Tout décocher")
        btn_visible = self._action_button("Colonnes affichées")
        btn_visible.setToolTip("Reprend les colonnes visibles dans le tableau.")
        from sme_gend.ui.check_all import sync_check_all
        def sync_columns(*_):
            sync_check_all(btn_all, self._boxes, [key for key, box in self._boxes.items() if box.isChecked()])
        btn_all.clicked.connect(lambda _checked=False: self._set_all(not all(box.isChecked() for box in self._boxes.values())))
        for box in self._boxes.values():
            box.toggled.connect(sync_columns)
        sync_columns()
        btn_none.clicked.connect(lambda _checked=False: self._set_all(False))
        btn_visible.clicked.connect(self._apply_visible)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(btn_all)
        tools.addWidget(btn_none)
        tools.addWidget(btn_visible)
        tools.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMaximumHeight(280)
        host = QWidget()
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.addWidget(columns)
        host_lay.addLayout(tools)
        scroll.setWidget(host)

        self._hint = QLabel("Cochez au moins une colonne.")
        self._hint.setObjectName("settingsPathsHint")
        self._hint.hide()

        btn_excel = self._action_button("Excel…")
        btn_pdf = self._action_button("PDF…")
        btn_print = self._action_button("Imprimer…")
        btn_print.setObjectName("primaryButton")
        btn_cancel = self._action_button("Annuler")
        btn_excel.clicked.connect(lambda _checked=False: self._accept("excel"))
        btn_pdf.clicked.connect(lambda _checked=False: self._accept("pdf"))
        btn_print.clicked.connect(lambda _checked=False: self._accept("print"))
        btn_cancel.clicked.connect(self.reject)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch()
        actions.addWidget(btn_excel)
        actions.addWidget(btn_pdf)
        actions.addWidget(btn_print)
        actions.addWidget(btn_cancel)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(intro)
        lay.addWidget(scroll, stretch=1)
        lay.addWidget(self._hint)
        lay.addLayout(actions)

    def _action_button(self, text: str) -> QPushButton:
        """Dans un QDialog, le premier clic ne fait que le focus si autoDefault est actif."""
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setDefault(False)
        return button

    def action(self) -> str:
        return self._action

    def selected_keys(self) -> list[str]:
        return [key for key, _label, _width in PRINT_COLUMNS if self._boxes[key].isChecked()]

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            box.setChecked(checked)
        self._hint.hide()

    def _apply_visible(self) -> None:
        wanted = set(self._visible_keys)
        if not wanted:
            self._set_all(True)
            return
        for key, box in self._boxes.items():
            box.setChecked(key in wanted)
        self._hint.hide()

    def _accept(self, action: str) -> None:
        if not self.selected_keys():
            self._hint.show()
            return
        self._action = action
        self.accept()
