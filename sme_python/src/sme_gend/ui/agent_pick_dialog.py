"""Sélection d'un agent pour saisie gains & pertes."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import PersonnelSearchHit
from sme_gend.ui.grade_sort_table_item import (
    GradeSortTableWidgetItem,
)
from sme_gend.services.personnel_list_order import GradeSortHelper


class AgentPickDialog(QDialog):
    """Recherche un agent et retourne ID legacy + grade + nom."""

    _DEBOUNCE_MS = 300

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        active_effectif_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._active_effectif_only = active_effectif_only
        self._hits: list[PersonnelSearchHit] = []
        self._hits_raw: list[PersonnelSearchHit] = []
        self._selected: PersonnelSearchHit | None = None
        self._search_generation = 0
        self._search_worker = None
        self._grade_sort = GradeSortHelper()
        from sme_gend.repositories.grade_repository import GradeRepository

        try:
            self._grades: list[GradeRefRow] = GradeRepository(settings).fetch_all()
        except Exception:
            self._grades = []

        self.setWindowTitle("Choisir un agent")
        self.setMinimumSize(760, 460)

        if active_effectif_only:
            hint_text = (
                "Recherchez par nom ou matricule (2 caractères min.) dans l'effectif "
                "du mois affiché. Filtrez éventuellement par grade."
            )
        else:
            hint_text = (
                "Recherchez par nom ou matricule (2 caractères min.). Filtrez éventuellement "
                "par grade. Les agents hors effectif sont inclus (sorties, décès)."
            )
        hint = QLabel(hint_text)
        hint.setWordWrap(True)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Nom, matricule…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._schedule_search)

        self._combo_grade = QComboBox()
        self._combo_grade.setMinimumWidth(160)
        self._combo_grade.addItem("(Tous les grades)", None)
        from sme_gend.services.grade_label_format import grade_combo_items

        for label, code in grade_combo_items(self._grades):
            self._combo_grade.addItem(label, code)
        self._combo_grade.currentIndexChanged.connect(self._on_grade_filter_changed)

        self._count = QLabel("—")

        top = QHBoxLayout()
        top.addWidget(self._search, stretch=1)
        top.addWidget(QLabel("Grade"))
        top.addWidget(self._combo_grade)
        top.addWidget(self._count)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Grade", "Noms & prénoms", "Matricule", "Statut"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.doubleClicked.connect(self._accept_selection)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.accepted.connect(self._accept_selection)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(top)
        lay.addWidget(self._table, stretch=1)
        lay.addWidget(bb)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_search)

    def selected_legacy_id(self) -> str | None:
        if self._selected is None:
            return None
        return (self._selected.row.id or "").strip() or None

    def selected_grade(self) -> str | None:
        if self._selected is None:
            return None
        return (self._selected.row.grade or "").strip() or None

    def selected_nom(self) -> str | None:
        if self._selected is None:
            return None
        return (self._selected.row.noms or "").strip() or None

    def selected_row(self) -> ListeGeneraleRow | None:
        if self._selected is None:
            return None
        return self._selected.row

    def selected_kind(self) -> str | None:
        if self._selected is None:
            return None
        return self._selected.kind

    def _schedule_search(self) -> None:
        self._search_generation += 1
        self._hits = []
        self._hits_raw = []
        self._table.setRowCount(0)
        self._count.setText("Recherche…")
        self._timer.start(self._DEBOUNCE_MS)

    def _on_grade_filter_changed(self, _index: int = 0) -> None:
        if self._hits_raw:
            self._apply_grade_filter()

    def _apply_grade_filter(self) -> None:
        from sme_gend.ui.personnel_list_filters import combo_filter_value, grade_matches_filter

        grade_code = combo_filter_value(self._combo_grade)
        grade_disp = self._combo_grade.currentText()
        if grade_code:
            self._hits = [
                h
                for h in self._hits_raw
                if grade_matches_filter(
                    h.row.grade,
                    grade_code,
                    combo_display=grade_disp,
                    grades=self._grades,
                )
            ]
        else:
            self._hits = list(self._hits_raw)
        self._fill_table()

    def _do_search(self) -> None:
        term = self._search.text().strip()
        generation = self._search_generation
        if len(term) < 2:
            self._hits = []
            self._hits_raw = []
            self._fill_table()
            return
        self._hits = []
        self._hits_raw = []
        self._fill_table()
        self._count.setText("Recherche…")
        settings = self._settings

        def work() -> list[PersonnelSearchHit]:
            from sme_gend.services.personnel_lookup_service import PersonnelLookupService

            svc = PersonnelLookupService(settings)
            if self._active_effectif_only:
                return svc.search_active_effectif(term)
            return svc.search_extended(term)

        def done(hits: list[PersonnelSearchHit]) -> None:
            if generation != self._search_generation:
                return
            self._hits_raw = hits
            extra: list[str] = []
            for h in hits:
                g = (h.row.grade or "").strip()
                if g and g not in extra:
                    extra.append(g)
            from sme_gend.services.grade_label_format import grade_combo_items

            self._combo_grade.blockSignals(True)
            self._combo_grade.clear()
            self._combo_grade.addItem("(Tous les grades)", None)
            for label, code in grade_combo_items(self._grades, extra_raw=extra):
                self._combo_grade.addItem(label, code)
            self._combo_grade.setCurrentIndex(0)
            self._combo_grade.blockSignals(False)
            self._grade_sort = GradeSortHelper(self._grades)
            self._apply_grade_filter()

        def failed(exc: BaseException) -> None:
            if generation != self._search_generation:
                return
            self._count.setText("Erreur de recherche")
            from sme_gend.services.mysql_health import format_mysql_error_for_user

            QMessageBox.critical(self, "Recherche d’agent", format_mysql_error_for_user(exc))

        from sme_gend.ui.bg_task import run_in_background

        self._search_worker = run_in_background(self, work, done, on_error=failed)

    def _fill_table(self) -> None:
        self._table.setRowCount(len(self._hits))
        for i, hit in enumerate(self._hits):
            row = hit.row
            self._table.setItem(
                i, 0, GradeSortTableWidgetItem(row.grade, self._grade_sort),
            )
            nom = (row.noms or "").strip()
            self._table.setItem(i, 1, QTableWidgetItem(nom))
            self._table.setItem(i, 2, QTableWidgetItem(row.id or row.matricule_militaire or ""))
            self._table.setItem(i, 3, QTableWidgetItem(hit.status_label()))
        self._count.setText(f"{len(self._hits)} résultat(s)")

    def _accept_selection(self) -> None:
        idx = self._table.currentRow()
        if idx < 0 or idx >= len(self._hits):
            return
        self._selected = self._hits[idx]
        self.accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._search.setFocus()
