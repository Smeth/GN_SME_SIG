"""Référentiels médailles et décorations / ordres (MySQL ref_*)."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from PySide6.QtCore import Qt
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.ui.confirm_dialogs import confirm_overwrite_file, confirm_yes_no

from sme_gend.models.distinction_ref import DistinctionRefRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.ref_table_model import RefTableModel
from sme_gend.ui.view_lifecycle import ViewDataState


def _parse_int_opt(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    return int(s)


class _DistinctionRefDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        existing: DistinctionRefRow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self._code = QLineEdit()
        self._court = QLineEdit()
        self._long = QLineEdit()
        self._cat = QLineEdit()
        self._cat.setPlaceholderText("Médaille, ordre national, distinction…")
        self._ordre = QLineEdit()
        self._actif = QCheckBox("Actif (proposé à la saisie dossier)")
        self._actif.setChecked(True)
        if existing:
            self._code.setText(existing.code)
            self._court.setText(existing.libelle_court or "")
            self._long.setText(existing.libelle_long or "")
            self._cat.setText(existing.categorie or "")
            if existing.ordre_preseance is not None:
                self._ordre.setText(str(existing.ordre_preseance))
            self._actif.setChecked(existing.actif)
        form = QFormLayout()
        form.addRow("Code *", self._code)
        form.addRow("Libellé court", self._court)
        form.addRow("Libellé long", self._long)
        form.addRow("Catégorie", self._cat)
        form.addRow("Ordre préséance", self._ordre)
        form.addRow("", self._actif)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _accept(self) -> None:
        if not self._code.text().strip():
            QMessageBox.warning(self, "Référentiel", "Le code est obligatoire.")
            return
        self.accept()

    def values(self) -> dict:
        try:
            ordre = _parse_int_opt(self._ordre.text())
        except ValueError:
            ordre = None
        return {
            "code": self._code.text().strip(),
            "libelle_court": self._court.text().strip() or None,
            "libelle_long": self._long.text().strip() or None,
            "categorie": self._cat.text().strip() or None,
            "ordre_preseance": ordre,
            "actif": self._actif.isChecked(),
        }


class _RefTab(QWidget):
    _HEADERS = ["Code", "Libellé", "Catégorie", "Préséance", "Actif"]

    def __init__(
        self,
        service: ReferentielService,
        *,
        kind: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._kind = kind
        self._rows: list[DistinctionRefRow] = []
        self._filtered: list[DistinctionRefRow] = []

        self._search = QLineEdit()
        self._search.setPlaceholderText("Rechercher…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)

        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.doubleClicked.connect(self._on_edit)

        self._btn_reload = QPushButton("Actualiser")
        self._btn_reload.clicked.connect(self.reload)
        self._btn_add = QPushButton("Nouveau…")
        self._btn_add.clicked.connect(self._on_add)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)

        bar = QHBoxLayout()
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._search, stretch=1)
        bar.addWidget(self._btn_add)
        bar.addWidget(self._btn_edit)
        bar.addWidget(self._btn_del)

        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self._table, stretch=1)

    def _fetch(self) -> list[DistinctionRefRow]:
        if self._kind == "medaille":
            return self._service.medailles()
        return self._service.decorations()

    def _insert(self, **kwargs) -> None:
        if self._kind == "medaille":
            self._service.medaille_insert(**kwargs)
        else:
            self._service.decoration_insert(**kwargs)

    def _update(self, ref_id: int, **kwargs) -> None:
        if self._kind == "medaille":
            self._service.medaille_update(ref_id, **kwargs)
        else:
            self._service.decoration_update(ref_id, **kwargs)

    def _delete(self, ref_id: int) -> None:
        if self._kind == "medaille":
            self._service.medaille_delete(ref_id)
        else:
            self._service.decoration_delete(ref_id)

    def reload(self) -> None:
        try:
            if not self._service.distinctions_tables_ready():
                QMessageBox.warning(
                    self,
                    "Médailles & décorations",
                    "Tables MySQL absentes. Relancez l'application après connexion MySQL "
                    "ou exécutez les migrations (import / paramètres).",
                )
                self._rows = []
            else:
                self._rows = self._fetch()
        except Exception as exc:
            QMessageBox.critical(self, "Erreur", str(exc))
            self._rows = []
        self._apply_filter()

    def _apply_filter(self) -> None:
        q = normalize_search_text(self._search.text().strip().casefold())
        if q:
            self._filtered = [
                r
                for r in self._rows
                if q in normalize_search_text(r.code.casefold())
                or q in normalize_search_text((r.libelle_court or "").casefold())
                or q in normalize_search_text(r.libelle_long)
                or q in normalize_search_text((r.categorie or "").casefold())
            ]
        else:
            self._filtered = list(self._rows)
        data = [
            [
                r.code,
                r.display_label(),
                r.categorie or "",
                "" if r.ordre_preseance is None else str(r.ordre_preseance),
                "Oui" if r.actif else "Non",
            ]
            for r in self._filtered
        ]
        self._model.set_data(self._HEADERS, data)

    def _selected(self) -> DistinctionRefRow | None:
        idx = self._table.selectionModel().currentIndex()
        row = idx.row()
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def _on_add(self) -> None:
        label = "médaille" if self._kind == "medaille" else "décoration"
        dlg = _DistinctionRefDialog(self, title=f"Nouvelle {label}")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._insert(**dlg.values())
        except Exception as exc:
            QMessageBox.critical(self, "Référentiel", str(exc))
            return
        self.reload()

    def _on_edit(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Référentiel", "Sélectionnez une ligne.")
            return
        label = "médaille" if self._kind == "medaille" else "décoration"
        dlg = _DistinctionRefDialog(self, title=f"Modifier la {label}", existing=row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._update(row.id, **dlg.values())
        except Exception as exc:
            QMessageBox.critical(self, "Référentiel", str(exc))
            return
        self.reload()

    def _on_delete(self) -> None:
        row = self._selected()
        if row is None:
            QMessageBox.information(self, "Référentiel", "Sélectionnez une ligne.")
            return
        if QMessageBox.question(
            self,
            "Supprimer",
            f"Supprimer « {row.display_label()} » ({row.code}) ?\n\n"
            "Impossible si des attributions agent existent encore.",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._delete(row.id)
        except Exception as exc:
            QMessageBox.critical(self, "Référentiel", str(exc))
            return
        self.reload()


class DistinctionsView(QWidget):
    """Médailles et décorations / ordres — référentiels MySQL."""

    def __init__(self, service: ReferentielService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DistinctionsView")
        self._service = service
        self._view_data = ViewDataState(stale=True)

        hint = QLabel(
            "Référentiels officiels des <b>médailles</b> et des <b>décorations / ordres</b>. "
            "Les attributions par agent se gèrent dans le dossier personnel (onglet Dossier complet). "
            "Les archives SME mensuelles ne contiennent pas ces données : saisie ou import dédié."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)

        self._lbl_stats = QLabel("")
        self._lbl_stats.setObjectName("gainPerteCount")

        self._btn_template = QPushButton("Modèle Excel…")
        self._btn_template.setToolTip("Télécharge un classeur modèle (attributions + référentiels).")
        self._btn_template.clicked.connect(self._on_template)
        self._btn_import = QPushButton("Importer Excel / CSV…")
        self._btn_import.setToolTip(
            "Importe les feuilles Medailles_ref / Decorations_ref (optionnel) "
            "puis les attributions (feuille Attributions)."
        )
        self._btn_import.clicked.connect(self._on_import)
        self._btn_export = QPushButton("Exporter attributions…")
        self._btn_export.setToolTip(
            "Exporte les distinctions des agents de la liste générale (mois consulté)."
        )
        self._btn_export.clicked.connect(self._on_export)

        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        tool_row.addWidget(self._btn_template)
        tool_row.addWidget(self._btn_import)
        tool_row.addWidget(self._btn_export)
        tool_row.addStretch()
        tool_row.addWidget(self._lbl_stats)

        tabs = QTabWidget()
        self._tab_med = _RefTab(service, kind="medaille")
        self._tab_dec = _RefTab(service, kind="decoration")
        tabs.addTab(self._tab_med, "Médailles")
        tabs.addTab(self._tab_dec, "Décorations & ordres")

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addLayout(tool_row)
        lay.addWidget(tabs, stretch=1)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def mark_view_stale(self) -> None:
        self.mark_dirty()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def reload(self) -> None:
        self._service.ensure_distinctions_initialized()
        self._tab_med.reload()
        self._tab_dec.reload()
        self._refresh_stats()
        self._view_data.finish_load()

    def _refresh_stats(self) -> None:
        from sme_gend.repositories.mysql_distinction_repository import (
            MySQLDistinctionRepository,
        )

        n_med = len(self._service.medailles())
        n_dec = len(self._service.decorations())
        n_attr = MySQLDistinctionRepository(self._service.settings).count_attributions()
        self._lbl_stats.setText(
            f"{n_med} médaille(s) réf. · {n_dec} décoration(s) réf. · {n_attr} attribution(s)"
        )

    def _on_template(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le modèle",
            str(Path.home() / "Modele_distinctions.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        try:
            from sme_gend.services.distinction_excel_export import write_distinction_import_template

            write_distinction_import_template(Path(dest), settings=self._service.settings)
        except Exception as exc:
            QMessageBox.critical(self, "Modèle", str(exc))
            return
        QMessageBox.information(self, "Modèle", f"Fichier enregistré :\n{dest}")

    def _on_import(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "referentiels.distinctions"):
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Importer distinctions",
            str(Path.home()),
            "Excel (*.xlsx);;CSV (*.csv)",
        )
        if not path:
            return
        if not confirm_yes_no(
            self,
            "Confirmer l'import",
            "Appliquer l'import depuis ce fichier ?\n\n"
            "Les référentiels des feuilles Medailles_ref / Decorations_ref seront "
            "créés ou mis à jour, puis les attributions seront ajoutées "
            "(doublons agent + distinction + date ignorés).",
            default_no=True,
        ):
            return
        try:
            from sme_gend.services.distinction_excel_import import apply_distinction_import

            result = apply_distinction_import(self._service.settings, Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Import", str(exc))
            return
        msg = (
            f"Réf. médailles : {result.refs_medailles_upserted}\n"
            f"Réf. décorations : {result.refs_decorations_upserted}\n"
            f"Attributions créées : {result.attributions_created}\n"
            f"Doublons ignorés : {result.skipped_duplicate}"
        )
        if result.errors:
            msg += "\n\nAvertissements / échecs :\n" + "\n".join(result.errors[:12])
            if len(result.errors) > 12:
                msg += f"\n… (+{len(result.errors) - 12})"
        QMessageBox.information(self, "Import distinctions", msg)
        self.reload()

    def _on_export(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return
        structures = sorted({(s.code or "").strip() for s in self._service.structures() if s.code})
        dlg = QDialog(self)
        dlg.setWindowTitle("Exporter les attributions")
        combo = QComboBox()
        combo.addItem("Toutes les structures", "")
        for code in structures:
            combo.addItem(code, code)
        form = QFormLayout()
        form.addRow("Limiter à la structure", combo)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay = QVBoxLayout(dlg)
        lay.addLayout(form)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        struct_code = str(combo.currentData() or "") or None
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter attributions",
            str(Path.home() / "Distinctions_attributions.xlsx"),
            "Excel (*.xlsx)",
        )
        if not dest:
            return
        if not confirm_overwrite_file(self, dest):
            return
        lines = []
        try:
            from sme_gend.services.distinction_excel_export import (
                collect_distinction_export_lines,
                export_distinctions_workbook,
            )

            lines = collect_distinction_export_lines(
                self._service.settings,
                structure_code=struct_code,
            )
            title = "Attributions médailles et décorations"
            if struct_code:
                title += f" — {struct_code}"
            export_distinctions_workbook(
                Path(dest),
                lines=lines,
                grades=self._service.grades(),
                title=title,
                settings=self._service.settings,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export", str(exc))
            return
        QMessageBox.information(
            self,
            "Export",
            f"{len(lines)} ligne(s) exportée(s).\n\n{dest}",
        )

    def apply_runtime_after_config_change(self) -> None:
        self._view_data.mark_stale()
        if self.isVisible():
            self.reload()
