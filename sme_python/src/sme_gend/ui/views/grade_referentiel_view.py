"""Référentiel grades — vue détaillée avec insigne et ordre de préséance."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSize
from PySide6.QtGui import QIcon, QPixmap, QImage, QIntValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.services.personnel_media import (
    grade_insignia_path,
)
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.responsive_panels import scroll_panel
from sme_gend.services.reference_policy import grade_values
from sme_gend.services.auth_guard import session_has_permission


class GradeTableModel(QAbstractTableModel):
    """Tableau grades avec miniature d'insigne."""

    COL_INSIGNIA = 0
    COL_CODE = 1
    COL_LIBELLE = 2
    COL_PREF = 3
    COL_EFFECTIF = 4

    _HEADERS = ("", "Code", "Libellé", "Préséance", "Effectif renseigné")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: list[GradeRefRow] = []
        self._icons = {}

    def set_media(self, media):
        self._icons = {code: QIcon(QPixmap.fromImage(image)) for code, (image, path) in media.items()
                       if not image.isNull()}

    def set_rows(self, rows: list[GradeRefRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, row: int) -> GradeRefRow | None:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if col == self.COL_INSIGNIA:
            if role == Qt.ItemDataRole.DecorationRole:
                return self._icons.get(row.code)
            if role == Qt.ItemDataRole.ToolTipRole:
                if row.code in self._icons:
                    return "Insigne configuré"
                return "Aucun insigne — ajoutez une image dans le panneau de droite"
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return int(Qt.AlignmentFlag.AlignCenter)
            return None

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (self.COL_PREF, self.COL_EFFECTIF):
                return int(Qt.AlignmentFlag.AlignCenter)
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if col == self.COL_CODE:
            return row.code
        if col == self.COL_LIBELLE:
            return row.libelle
        if col == self.COL_PREF:
            return "" if row.pref_ord is None else str(row.pref_ord)
        if col == self.COL_EFFECTIF:
            return "" if row.effectif_g is None else str(row.effectif_g)
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._HEADERS[section]
        return str(section + 1)


class GradeEditDialog(QDialog):
    """Création / modification d'un grade avec aperçu d'insigne."""

    def __init__(
        self,
        parent,
        row: GradeRefRow | None = None,
        *,
        insignia_label: str | None = None,
        preview=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("GradeEditDialog")
        self.setWindowTitle("Nouveau grade" if row is None else "Modifier le grade")
        self.setMinimumWidth(560)
        self._insignia_label = insignia_label or (row.libelle if row else None)
        self._pending_insignia = None
        self._preview_image = preview[0] if preview else QImage()

        self._code = QLineEdit()
        self._libelle = QLineEdit()
        self._pref = QLineEdit()
        self._eff = QLineEdit()
        self._pref.setValidator(QIntValidator(0, 2147483647, self))
        self._eff.setValidator(QIntValidator(0, 2147483647, self))
        self._code.setMaxLength(64)
        self._libelle.setMaxLength(512)
        self._code.setPlaceholderText("Ex. MDL")
        self._libelle.setPlaceholderText("Ex. Maréchal des logis")
        self._pref.setPlaceholderText("1 = plus élevé")
        self._eff.setPlaceholderText("Optionnel")

        if row:
            self._code.setText(row.code)
            self._code.setReadOnly(True)
            self._libelle.setText(row.libelle)
            self._pref.setText("" if row.pref_ord is None else str(row.pref_ord))
            self._eff.setText("" if row.effectif_g is None else str(row.effectif_g))

        form = QFormLayout()
        form.addRow("Code *", self._code)
        form.addRow("Libellé *", self._libelle)
        pref_hint = QLabel("Entier positif ou nul. Plus petit = plus élevé ; en cas d'égalité, tri par code.")
        pref_hint.setWordWrap(True)
        pref_hint.setObjectName("GradeEditHint")
        form.addRow("Préséance", self._pref)
        form.addRow("", pref_hint)
        form.addRow("Effectif renseigné", self._eff)

        self._insignia_preview = QLabel()
        self._insignia_preview.setObjectName("GradeDetailInsignia")
        self._insignia_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._insignia_preview.setMinimumSize(160, 120)
        self._insignia_preview.setMaximumSize(220, 160)
        self._insignia_status = QLabel()
        self._insignia_status.setObjectName("GradeDetailMeta")
        self._insignia_status.setWordWrap(True)
        self._btn_insignia = QPushButton("Choisir une image d'insigne…")
        self._btn_insignia.clicked.connect(self._pick_insignia)
        self._refresh_insignia_preview()

        preview_col = QVBoxLayout()
        preview_title = QLabel("Insigne")
        preview_title.setProperty("subheading", True)
        preview_col.addWidget(preview_title)
        preview_col.addWidget(self._insignia_preview, alignment=Qt.AlignmentFlag.AlignHCenter)
        preview_col.addWidget(self._insignia_status)
        preview_col.addWidget(self._btn_insignia)
        preview_col.addStretch()

        preview_frame = QFrame()
        preview_frame.setObjectName("GradeEditPreview")
        preview_frame.setLayout(preview_col)

        body = QHBoxLayout()
        body.setSpacing(20)
        body.addLayout(form, stretch=3)
        body.addWidget(preview_frame, stretch=2)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._validate)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(body)
        lay.addWidget(bb)

    def _refresh_insignia_preview(self) -> None:
        label = (self._libelle.text().strip() or self._insignia_label or "").strip()
        fallback = self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        ).pixmap(72, 72)
        pix = (QPixmap.fromImage(self._preview_image).scaled(200, 140, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation) if not self._preview_image.isNull() else None)
        if pix is not None:
            self._insignia_preview.setPixmap(pix)
            self._insignia_status.setText("Image sélectionnée — sera enregistrée avec le grade." if self._pending_insignia
                                         else f"Insigne enregistré pour « {label} ».")
        else:
            self._insignia_preview.setPixmap(fallback)
            self._insignia_status.setText(
                "Aucune image pour ce grade.\nUtilisez le bouton ci-dessous ou le panneau détail."
            )

    def _pick_insignia(self) -> None:
        label = (self._libelle.text().strip() or self._insignia_label or "").strip()
        if not label:
            QMessageBox.warning(self, "Insigne", "Saisissez d'abord le libellé du grade.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Insigne — {label}",
            str(Path.home()),
            "Images (*.png *.jpg *.jpeg *.webp *.svg)",
        )
        if not path:
            return
        image = run_with_progress(self, "Aperçu de l'insigne", "Lecture de l'image…", lambda: QImage(path))
        if image is None or image.isNull():
            QMessageBox.warning(self, "Insigne", "Choisissez une image valide.")
            return
        self._pending_insignia = Path(path)
        self._preview_image = image
        self._insignia_label = label
        self._refresh_insignia_preview()

    def _validate(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Grade", str(exc))
            return
        self.accept()

    def values(self) -> tuple[str, str, int | None, int | None]:
        po = self._pref.text().strip()
        eg = self._eff.text().strip()
        return grade_values(self._code.text(), self._libelle.text(), po, eg)


class _GradeDetailPanel(QFrame):
    """Panneau latéral — fiche du grade sélectionné (sans défilement)."""

    _INSIGNIA_H = 136

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("GradeDetailPanel")
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)

        self._empty = QLabel("Sélectionnez un grade.")
        self._empty.setObjectName("GradeDetailEmpty")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self._content = QWidget()
        content_lay = QVBoxLayout(self._content)
        content_lay.setSpacing(10)
        content_lay.setContentsMargins(0, 0, 0, 0)

        self._badge = QLabel()
        self._badge.setObjectName("GradeDetailBadge")
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._insignia_frame = QFrame()
        self._insignia_frame.setObjectName("GradeDetailInsigniaFrame")
        self._insignia_frame.setFixedHeight(self._INSIGNIA_H)
        insignia_lay = QVBoxLayout(self._insignia_frame)
        insignia_lay.setContentsMargins(10, 10, 10, 10)
        self._insignia = QLabel()
        self._insignia.setObjectName("GradeDetailInsignia")
        self._insignia.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._insignia.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        insignia_lay.addWidget(self._insignia)

        self._title = QLabel()
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setObjectName("GradeDetailTitle")
        self._title.setWordWrap(True)
        self._title.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._code_lbl = QLabel()
        self._code_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self._code_lbl.setObjectName("GradeDetailCode")
        self._code_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self._meta = QLabel()
        self._meta.setObjectName("GradeDetailMeta")
        self._meta.setWordWrap(True)
        self._meta.setTextFormat(Qt.TextFormat.RichText)

        self._path_hint = QLabel()
        self._path_hint.setObjectName("GradeDetailPathHint")
        self._path_hint.setWordWrap(True)

        content_lay.addWidget(self._badge, alignment=Qt.AlignmentFlag.AlignHCenter)
        content_lay.addWidget(self._insignia_frame)
        content_lay.addWidget(self._title)
        content_lay.addWidget(self._code_lbl)
        content_lay.addWidget(self._meta)
        content_lay.addWidget(self._path_hint)
        content_lay.addStretch(1)

        self._stack = QVBoxLayout()
        self._stack.setContentsMargins(16, 16, 16, 12)
        self._stack.setSpacing(10)
        self._stack.addWidget(self._empty, stretch=1)
        self._stack.addWidget(self._content, stretch=1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(self._stack, stretch=1)

        self._btn_edit: QPushButton | None = None
        self._btn_insignia: QPushButton | None = None
        self._btn_del: QPushButton | None = None
        self._current_insignia_label = ""

        self.show_empty()

    def _insignia_target_size(self) -> tuple[int, int]:
        m = self._insignia_frame.layout().contentsMargins()
        w = max(120, self._insignia_frame.width() - m.left() - m.right())
        h = max(80, self._INSIGNIA_H - m.top() - m.bottom())
        return w, h

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._content.isVisible():
            return
        pix = getattr(self, "_cached_insignia", None)
        if pix is None:
            return
        w, h = self._insignia_target_size()
        self._insignia.setPixmap(pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))

    def bind_actions(
        self,
        *,
        on_edit,
        on_insignia,
        on_delete,
    ) -> None:
        for w in (self._btn_edit, self._btn_insignia, self._btn_del):
            if w is not None:
                w.deleteLater()

        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.setObjectName("GradeDetailPrimaryBtn")
        self._btn_edit.clicked.connect(on_edit)

        self._btn_insignia = QPushButton("Insigne (image)…")
        self._btn_insignia.clicked.connect(on_insignia)

        self._btn_del = QPushButton("Supprimer")
        self._btn_del.setProperty("destructive", True)
        self._btn_del.clicked.connect(on_delete)

        btn_row = QVBoxLayout()
        btn_row.setSpacing(8)
        btn_row.setContentsMargins(16, 0, 16, 16)
        btn_row.addWidget(self._btn_edit)
        btn_row.addWidget(self._btn_insignia)
        btn_row.addWidget(self._btn_del)
        self.layout().addLayout(btn_row)

    def set_actions_enabled(self, enabled: bool) -> None:
        for w in (self._btn_edit, self._btn_insignia, self._btn_del):
            if w is not None:
                w.setEnabled(enabled)

    def show_empty(self) -> None:
        self._content.hide()
        self._empty.show()
        self.set_actions_enabled(False)

    def show_grade(self, row: GradeRefRow, *, usage_count: int | None, media=None) -> None:
        self._empty.hide()
        self._content.show()
        self.set_actions_enabled(True)

        pref = row.pref_ord
        if pref is not None:
            self._badge.setText(f"Préséance {pref}")
            self._badge.show()
        else:
            self._badge.hide()

        self._title.setText(row.libelle or "—")
        self._code_lbl.setText(f"Code : {row.code}")

        label = (row.libelle or row.code or "").strip()
        self._current_insignia_label = label
        fallback = self.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxInformation
        ).pixmap(72, 72)
        w, h = self._insignia_target_size()
        image, path = media if media else (QImage(), None)
        pix = QPixmap.fromImage(image) if not image.isNull() else None
        self._cached_insignia = pix
        if pix is not None:
            self._insignia.setPixmap(pix)
            insigne_txt = "● Insigne configuré"
            insigne_color = "#2e7d32"
        else:
            self._insignia.setPixmap(fallback)
            insigne_txt = "○ Aucun insigne"
            insigne_color = "#e65100"

        eff = "—" if row.effectif_g is None else str(row.effectif_g)
        usage = (
            f"{usage_count} agent(s)"
            if usage_count is not None
            else "—"
        )
        pref_txt = (
            f"{pref} (plus petit = plus élevé)"
            if pref is not None
            else "Non renseignée"
        )

        self._meta.setText(
            f"<p><span style='color:{insigne_color};'>{insigne_txt}</span></p>"
            f"<p><b>Préséance :</b> {pref_txt}</p>"
            f"<p><b>Effectif renseigné :</b> {eff}</p><p><b>Agents (archives comprises) :</b> {usage}</p>"
        )

        if path is not None:
            self._path_hint.setText("Insigne associé à ce grade.")
            self._path_hint.setToolTip(str(path))
        else:
            self._path_hint.setText("Aucune image — utilisez « Insigne (image)… »")
            self._path_hint.setToolTip("")


class GradesView(QWidget):
    """Référentiel grades — liste + fiche détail avec insigne."""

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("GradesView")
        self._service = service
        self._all_rows: list[GradeRefRow] = []
        self._visible_rows: list[GradeRefRow] = []
        self._view_data = ViewDataState(stale=True)
        self._reload_busy = False
        self._reload_again = False
        self._media = {}
        self._usage = {}

        header = QFrame()
        header.setObjectName("GradeReferentielHeader")
        header_lay = QVBoxLayout(header)
        header_lay.setContentsMargins(16, 14, 16, 14)
        title = QLabel("Référentiel des grades")
        title.setObjectName("GradeReferentielTitle")
        subtitle = QLabel(
            "Préséance : plus le nombre est petit, plus le grade est élevé. "
            "L’insigne s’affiche sur les fiches."
            " Référentiel global : les changements s'appliquent à tous les mois."
        )
        subtitle.setObjectName("GradeReferentielSubtitle")
        subtitle.setWordWrap(True)
        header_lay.addWidget(title)
        header_lay.addWidget(subtitle)

        self._model = GradeTableModel(self)
        self._table = QTableView()
        self._table.setObjectName("GradeReferentielTable")
        self._table.setModel(self._model)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(46)
        self._table.horizontalHeader().setSectionResizeMode(
            GradeTableModel.COL_INSIGNIA, QHeaderView.ResizeMode.Fixed
        )
        self._table.horizontalHeader().setSectionResizeMode(
            GradeTableModel.COL_CODE, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            GradeTableModel.COL_LIBELLE, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            GradeTableModel.COL_PREF, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            GradeTableModel.COL_EFFECTIF, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.setColumnWidth(GradeTableModel.COL_INSIGNIA, 52)
        self._table.setIconSize(QSize(36, 36))
        self._table.doubleClicked.connect(self._on_edit)

        self._detail = _GradeDetailPanel()
        self._detail.bind_actions(
            on_edit=self._on_edit,
            on_insignia=self._on_grade_insignia,
            on_delete=self._on_delete,
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("GradeReferentielSplitter")
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(10)

        self._search = QLineEdit()
        self._search.setObjectName("GradeReferentielSearch")
        self._search.setPlaceholderText("Rechercher par code ou libellé…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _text: self._apply_filter())

        self._count_lbl = QLabel()
        self._count_lbl.setObjectName("GradeReferentielCount")

        toolbar = QHBoxLayout()
        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouveau grade…")
        self._btn_add.setObjectName("GradeToolbarPrimaryBtn")
        self._btn_add.clicked.connect(self._on_add)
        toolbar.addWidget(self._btn)
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._btn_add)

        left_lay.addLayout(toolbar)
        left_lay.addWidget(self._count_lbl)
        left_lay.addWidget(self._table, stretch=1)

        splitter.addWidget(left)
        splitter.addWidget(scroll_panel(self._detail))
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([640, 340])

        body_lay = QVBoxLayout()
        body_lay.setContentsMargins(16, 0, 16, 16)
        body_lay.addWidget(splitter, stretch=1)

        lay = QVBoxLayout(self)
        lay.setSpacing(0)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(header)
        lay.addLayout(body_lay)

        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()
        if self._reload_busy:
            self._reload_again = True

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def reload(self) -> None:
        if self._reload_busy:
            self._reload_again = True
            return
        from copy import copy
        service = copy(self._service)
        self._reload_busy = True
        self._btn.setEnabled(False)
        self._btn_add.setEnabled(False)
        self._table.setEnabled(False)
        self._detail.set_actions_enabled(False)
        self._count_lbl.setText("Chargement des grades et des insignes…")
        selected = self._selected_grade()
        selected_code = selected.code if selected else None
        def work():
            from sme_gend.services.grade_media import invalidate_grade_media_cache
            invalidate_grade_media_cache()
            rows = service.grades()
            usage = service.grade_usage_counts()
            media = {}
            for row in rows:
                path = grade_insignia_path(row.code, rows)
                image = QImage(str(path)) if path else QImage()
                if not image.isNull():
                    image = image.scaled(240, 180, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
                media[row.code] = (image, path)
            return rows, usage, media
        def done(payload):
            self._reload_busy = False
            if self._reload_again:
                self._reload_again = False
                self.reload()
                return
            self._all_rows, self._usage, self._media = payload
            self._model.set_media(self._media)
            self._btn.setEnabled(True)
            self._btn_add.setEnabled(session_has_permission("referentiels.grades"))
            self._table.setEnabled(True)
            self._apply_filter(selected_code)
            self._view_data.finish_load()
        def failed(error):
            self._reload_busy = False
            self._reload_again = False
            self._btn.setEnabled(True)
            self._btn_add.setEnabled(session_has_permission("referentiels.grades"))
            self._table.setEnabled(True)
            self._count_lbl.setText("Chargement impossible — données précédentes conservées. Actualisez pour réessayer.")
            self._view_data.mark_stale()
            self._on_selection_changed()
            QMessageBox.warning(self, "Grades", str(error))
        self._worker = run_in_background(self, work, done, on_error=failed)

    def _apply_filter(self, selected_code=None) -> None:
        if selected_code is None:
            selected = self._selected_grade()
            selected_code = selected.code if selected else None
        q = normalize_search_text(self._search.text())
        rows = [r for r in self._all_rows if not q or q in normalize_search_text(r.code)
                or q in normalize_search_text(r.libelle)]
        self._visible_rows = rows
        self._model.set_rows(rows)
        self._count_lbl.setText(f"{len(rows)} grade(s) affiché(s) sur {len(self._all_rows)}"
                                if rows else "Aucun grade correspondant à la recherche.")
        if rows:
            index = next((i for i, r in enumerate(rows) if r.code == selected_code), 0)
            self._table.selectRow(index)
        self._on_selection_changed()

    def apply_runtime_after_config_change(self) -> None:
        self.mark_dirty()
        if self.isVisible():
            self.reload()

    def _selected_grade(self) -> GradeRefRow | None:
        idx = self._table.selectionModel().currentIndex()
        return self._model.row_at(idx.row()) if idx.isValid() else None

    def _on_selection_changed(self) -> None:
        row = self._selected_grade()
        if row is None:
            self._detail.show_empty()
            return
        self._detail.show_grade(row, usage_count=self._usage.get(row.code), media=self._media.get(row.code))
        permitted = not self._reload_busy and session_has_permission("referentiels.grades")
        self._detail.set_actions_enabled(permitted)
        used = self._usage.get(row.code, 0) > 0
        self._detail._btn_del.setEnabled(permitted and not used)
        self._detail._btn_del.setToolTip("Grade utilisé par des agents, archives comprises : suppression protégée." if used else "Supprimer ce grade inutilisé.")

    def _on_add(self) -> None:
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        if not session_has_permission("referentiels.grades"):
            return
        service = copy(self._service)
        dlg = GradeEditDialog(self)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            values, source = dlg.values(), dlg._pending_insignia
            if not confirm_yes_no(self, "Créer un grade", f"Créer le grade « {values[0]} » dans le référentiel global ?", default_no=True):
                continue
            if execute_reference_change(self, lambda: service.save_grade(*values, insignia_source=source, create=True)):
                self.reload()
                return

    def _on_edit(self) -> None:
        self._edit_grade()

    def _edit_grade(self, *, pick_image=False):
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        row = self._selected_grade()
        if row is None or not session_has_permission("referentiels.grades"):
            return
        service = copy(self._service)
        dlg = GradeEditDialog(self, row, preview=self._media.get(row.code))
        if pick_image:
            dlg._pick_insignia()
        while dlg.exec() == QDialog.DialogCode.Accepted:
            values, source = dlg.values(), dlg._pending_insignia
            if not confirm_yes_no(self, "Modifier le grade", "Enregistrer ces changements pour tous les mois ?", default_no=True):
                continue
            if execute_reference_change(self, lambda: service.save_grade(*values, expected=row, insignia_source=source)):
                self.reload()
                return

    def _on_grade_insignia(self) -> None:
        self._edit_grade(pick_image=True)

    def _on_delete(self) -> None:
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        row = self._selected_grade()
        if row is None or not session_has_permission("referentiels.grades"):
            return
        usage = self._usage.get(row.code, 0)
        if usage:
            QMessageBox.warning(self, "Grade utilisé", f"{usage} agent(s) utilisent ce grade, archives comprises. Suppression refusée.")
            return
        if not confirm_yes_no(self, "Supprimer le grade", f"Supprimer « {row.code} » du référentiel global ? Les grades utilisés sont protégés.", default_no=True):
            return
        service = copy(self._service)
        execute_reference_change(self, lambda: service.grade_delete(row.code, expected=row), draft=False)
        self.reload()
