"""Vues référentiels — lecture / édition Structures, Grades, Positions administratives."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableView,
    QHeaderView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.ref_table_model import RefTableModel
from sme_gend.ui.views.grade_referentiel_view import GradesView
from sme_gend.ui.views.fonction_referentiel_view import FonctionsView
from sme_gend.ui.views.organigram_quality_banner import OrganigramQualityBanner
from sme_gend.ui.views.structure_hub_view import StructureHubView
from sme_gend.ui.views.referentiel_coherence_view import ReferentielCoherenceView
from sme_gend.ui.views.subdivisions_hierarchy_view import SubdivisionsHierarchyView
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.responsive_panels import scroll_panel
from sme_gend.ui.responsive_panels import WrapLayout
from sme_gend.ui.bg_task import run_in_background
from sme_gend.services.reference_policy import position_values, fixed_presence, system_position
from sme_gend.services.auth_guard import session_has_permission


class StructuresView(QWidget):
    """Liste des structures, subdivisions d'arme et fonctions rattachées."""

    def __init__(
        self,
        service: ReferentielService,
        parent=None,
        *,
        navigate_to_fonctions: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._navigate_to_fonctions = navigate_to_fonctions

        self._quality_banner = OrganigramQualityBanner(service, self)

        self._hint = QLabel(
            "<b>Structures & subdivisions</b>"
        )
        self._hint.setWordWrap(True)
        self._hint.setObjectName("dashSubtitle")

        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)

        self._btn_maintenance = QToolButton()
        self._btn_maintenance.setText("Maintenance")
        self._btn_maintenance.setToolTip("Outils avancés (archives, nomenclature U→D→T)")
        self._btn_maintenance.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        maint_menu = QMenu(self)
        act_harm = maint_menu.addAction("Harmonisation des archives…")
        act_harm.setToolTip(
            "Fusion manuelle des variantes SubArme et structures (toutes périodes carrière)."
        )
        act_harm.triggered.connect(lambda: self.open_harmonization())
        act_nom = maint_menu.addAction("Nomenclature U→D→T…")
        act_nom.setToolTip("Hiérarchie globale des subdivisions (référentiel transversal).")
        act_nom.triggered.connect(self._open_nomenclature_dialog)
        self._btn_maintenance.setMenu(maint_menu)

        head = QHBoxLayout()
        head.addWidget(self._hint, stretch=1)
        self._quality_toggle = QToolButton()
        self._quality_toggle.setText('Détails du référentiel')
        self._quality_toggle.setCheckable(True)
        self._quality_toggle.toggled.connect(self._quality_banner.setVisible)
        self._quality_banner.hide()
        head.addWidget(self._quality_toggle)
        head.addWidget(self._btn)
        head.addWidget(self._btn_maintenance)

        self._hub_panel = StructureHubView(service, self)
        self._hub_panel.request_open_fonctions.connect(self._open_fonctions_for_structure)
        self._hub_panel.structures_changed.connect(self.reload)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        lay.addLayout(head)
        lay.addWidget(self._quality_banner)
        lay.addWidget(self._hub_panel, stretch=1)
        self._view_data = ViewDataState(stale=True)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()
        else:
            self._quality_banner.refresh()
            self._hub_panel.on_page_activated()

    def _activate_current_tab(self) -> None:
        self._hub_panel.on_page_activated()

    def open_harmonization(self, structure_code: str | None = None) -> None:
        self._open_harmonization_dialog(structure_code)

    def _open_harmonization_dialog(self, structure_code: str | None = None) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("Harmonisation référentiel (archives)")
        dlg.resize(980, 580)
        lay = QVBoxLayout(dlg)
        intro = QLabel(
            "Fusion manuelle des libellés SubArme et structures sur "
            "<b>toutes</b> les situations mensuelles enregistrées."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        panel = ReferentielCoherenceView(self._service, dlg)
        panel.on_page_activated(structure_code=structure_code)
        lay.addWidget(scroll_panel(panel), stretch=1)
        dlg.exec()

    def _open_fonctions_for_structure(self, code: str) -> None:
        if self._navigate_to_fonctions is None:
            QMessageBox.information(
                self,
                "Fonctions",
                "Ouvrez le module « Fonctions » dans le menu pour gérer les rattachements.",
            )
            return
        self._navigate_to_fonctions((code or "").strip())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def reload(self) -> None:
        self._hub_panel.mark_dirty()
        self._activate_current_tab()
        if not self._hub_panel._view_data.is_ready():
            self._quality_banner.mark_stale()
            self._view_data.mark_stale()
            return
        try:
            summary = self._hub_panel.quality_summary_if_loaded()
            self._quality_banner.refresh(summary)
        except Exception:
            pass
        self._view_data.finish_load()

    def apply_runtime_after_config_change(self) -> None:
        self._view_data.mark_stale()
        if self.isVisible():
            self.reload()

    def _open_nomenclature_dialog(self) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Nomenclature des subdivisions")
        dlg.resize(900, 560)
        lay = QVBoxLayout(dlg)
        intro = QLabel(
            "Hiérarchie transversale U→D→T des subdivisions."
        )
        intro.setWordWrap(True)
        lay.addWidget(intro)
        panel = SubdivisionsHierarchyView(self._service, dlg)
        panel.reload()
        lay.addWidget(scroll_panel(panel), stretch=1)
        dlg.exec()


# ── Positions administratives ─────────────────────────────────────────────────

class _PadminDialog(QDialog):
    """Dialogue de création / modification d'une position administrative."""

    def __init__(self, parent: QWidget, row: PadminRefRow | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RefEditDialog")
        self.setWindowTitle(
            "Nouvelle position administrative" if row is None else "Modifier la position"
        )
        self.setMinimumWidth(400)

        self._libelle = QLineEdit()
        self._libelle.setPlaceholderText("Libellé exact (ex : Présent)")
        self._ordre = QLineEdit()
        self._ordre.setValidator(QIntValidator(0, 2147483647, self))
        self._libelle.setMaxLength(256)
        self._old_label = row.libelle if row else None
        self._ordre.setPlaceholderText("Entier (ex : 1)")
        self._present = QCheckBox("Compte comme présent")

        if row is not None:
            self._libelle.setText(row.libelle)
            self._ordre.setText("" if row.ordre_tri is None else str(row.ordre_tri))
            self._present.setChecked(row.compte_comme_present)
            self._libelle.setReadOnly(system_position(row.libelle))
        self._libelle.textChanged.connect(self._sync_presence)
        self._sync_presence()

        form = QFormLayout()
        form.addRow("Libellé *", self._libelle)
        form.addRow("Ordre de tri", self._ordre)
        form.addRow("", self._present)

        note = QLabel("Référentiel global, tous les mois et archives compris. Les positions système "
                      "ne peuvent pas être renommées et leur règle de présence est imposée. "
                      "Pour les autres positions, la case s'applique à la liste des présents et aux états numériques.")
        note.setWordWrap(True)
        note.setTextFormat(Qt.TextFormat.RichText)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._validate)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(bb)

    def values(self) -> tuple[str, bool, int | None]:
        lib = self._libelle.text().strip()
        ord_txt = self._ordre.text().strip()
        return position_values(lib, self._present.isChecked(), ord_txt, old_label=self._old_label)

    def _sync_presence(self):
        required = fixed_presence(self._libelle.text())
        self._present.setEnabled(required is None)
        if required is not None:
            self._present.setChecked(required)
        self._present.setToolTip("Règle métier imposée pour cette position." if required is not None else
                                 "Détermine la présence de cette position personnalisée dans les états.")

    def _validate(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, "Position administrative", str(exc))
            return
        self.accept()


class PadminView(QWidget):
    """Référentiel des positions administratives (ref_position_admin — MySQL)."""

    def __init__(self, service: ReferentielService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._rows: list[PadminRefRow] = []
        self._usage = {}
        self._reload_busy = False
        self._reload_again = False

        self._hint = QLabel(
            "Positions administratives — référentiel global, archives comprises. "
            "Présent indique la règle effectivement utilisée dans les états. "
            "Les positions système ont une règle de présence fixe."
        )
        self._hint.setWordWrap(True)

        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.verticalHeader().hide()
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._on_edit)

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouvelle position…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        self._search_padmin = QLineEdit()
        self._search_padmin.setPlaceholderText("Rechercher position…")
        self._search_padmin.setClearButtonEnabled(True)
        self._search_padmin.textChanged.connect(lambda _text: self._filter_padmin_table())
        self._padmin_headers = ["Position administrative", "Présent", "Ordre", "Agents (tous les mois)"]
        self._count = QLabel()
        self._padmin_data: list[list] = []
        # Objets PadminRefRow filtrés, dans le même ordre que les lignes affichées
        # (self._rows reste la liste complète non filtrée).
        self._filtered_rows: list[PadminRefRow] = []

        bar = WrapLayout()
        bar.addWidget(self._btn_reload)
        self._search_padmin.setMinimumWidth(220)
        bar.addWidget(self._search_padmin)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addLayout(bar)
        lay.addWidget(self._count)
        lay.addWidget(self._table, stretch=1)
        self._view_data = ViewDataState(stale=True)
        self._table.selectionModel().selectionChanged.connect(self._update_actions)
        self._update_actions()

    def mark_dirty(self):
        self._view_data.mark_stale()
        if self._reload_busy:
            self._reload_again = True

    def mark_view_stale(self):
        self.mark_dirty()

    def on_page_activated(self):
        if self._view_data.needs_load() and not self._reload_busy:
            self.reload()

    def showEvent(self, event):
        super().showEvent(event)
        if not event.spontaneous():
            self.on_page_activated()

    def reload(self):
        from copy import copy
        if self._reload_busy:
            self._reload_again = True
            return
        service = copy(self._service)
        selected = self._current()
        label = selected.libelle if selected else None
        self._reload_busy = True
        self._btn_reload.setEnabled(False)
        self._table.setEnabled(False)
        self._count.setText("Chargement des positions…")
        self._update_actions()
        def work():
            return service.padmins(), service.padmin_usage_counts()
        def done(payload):
            self._reload_busy = False
            if self._reload_again:
                self._reload_again = False
                self.reload()
                return
            self._rows, self._usage = payload
            self._btn_reload.setEnabled(True)
            self._table.setEnabled(True)
            self._filter_padmin_table(label)
            self._view_data.finish_load()
        def failed(error):
            self._reload_busy = False
            self._reload_again = False
            self._view_data.mark_stale()
            self._btn_reload.setEnabled(True)
            self._table.setEnabled(True)
            self._count.setText("Chargement impossible — données précédentes conservées. Actualisez pour réessayer.")
            self._update_actions()
            QMessageBox.warning(self, "Positions administratives", str(error))
        self._worker = run_in_background(self, work, done, on_error=failed)

    def _filter_padmin_table(self, selected_label=None):
        if selected_label is None:
            selected = self._current()
            selected_label = selected.libelle if selected else None
        q = normalize_search_text(self._search_padmin.text())
        self._filtered_rows = [r for r in self._rows if not q or q in normalize_search_text(r.libelle)]
        data = []
        for row in self._filtered_rows:
            required = fixed_presence(row.libelle)
            present = required if required is not None else row.compte_comme_present
            data.append([row.libelle, "Oui" if present else "Non",
                         str(row.ordre_tri) if row.ordre_tri is not None else "",
                         str(self._usage.get(row.libelle, 0))])
        self._model.set_data(self._padmin_headers, data)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        if self._filtered_rows:
            index = next((i for i, r in enumerate(self._filtered_rows) if r.libelle == selected_label), 0)
            self._table.selectRow(index)
        self._count.setText(f"{len(data)} position(s) affichée(s) sur {len(self._rows)} — agents distincts, archives comprises."
                            if data else "Aucune position correspondant à la recherche.")
        self._update_actions()

    def _current(self):
        index = self._table.selectionModel().currentIndex()
        return self._filtered_rows[index.row()] if index.isValid() and index.row() < len(self._filtered_rows) else None

    def _selected_row(self):
        return self._table.selectionModel().currentIndex().row()

    def _update_actions(self):
        row = self._current()
        permitted = session_has_permission("referentiels.padmin") and not self._reload_busy
        self._btn_add.setEnabled(permitted)
        self._btn_edit.setEnabled(permitted and row is not None)
        protected = row is not None and (system_position(row.libelle) or self._usage.get(row.libelle, 0) > 0)
        self._btn_del.setEnabled(permitted and row is not None and not protected)
        self._btn_del.setToolTip("Position système ou utilisée par des agents : suppression protégée."
                                 if protected else "Supprimer une position inutilisée, archives comprises.")

    def apply_runtime_after_config_change(self):
        self.mark_dirty()
        if self.isVisible():
            self.reload()

    def _on_add(self):
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        if not session_has_permission("referentiels.padmin"):
            return
        service = copy(self._service)
        dlg = _PadminDialog(self)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.values()
            if execute_reference_change(self, lambda: service.padmin_insert(*values)):
                self.reload()
                return

    def _on_edit(self):
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        current = self._current()
        if current is None or not session_has_permission("referentiels.padmin"):
            return
        service = copy(self._service)
        dlg = _PadminDialog(self, current)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            new_libelle, compte, ordre = dlg.values()
            msg = "Enregistrer ces changements pour tous les mois ?"
            if new_libelle != current.libelle:
                msg += (f"\n\nRenommer « {current.libelle} » en « {new_libelle} » met à jour "
                        "les libellés et les liens des situations courantes et des archives. "
                        "Les traces d'audit déjà enregistrées restent conservées.")
            if not confirm_yes_no(self, "Référentiel global", msg, default_no=True):
                continue
            if execute_reference_change(self, lambda: service.padmin_update(
                    current.libelle, new_libelle, compte, ordre, expected=current)):
                self.reload()
                return

    def _on_delete(self):
        from copy import copy
        from sme_gend.ui.reference_tasks import execute_reference_change
        row = self._current()
        if row is None or not self._btn_del.isEnabled():
            return
        if not confirm_yes_no(self, "Supprimer une position inutilisée",
                f"Supprimer « {row.libelle} » du référentiel global ?\n\n"
                "L'opération sera refusée si une fiche ou une archive utilise cette position.", default_no=True):
            return
        service = copy(self._service)
        execute_reference_change(self, lambda: service.padmin_delete(row.libelle, expected=row), draft=False)
        self.reload()
