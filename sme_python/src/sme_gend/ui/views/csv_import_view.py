"""Vue d'import CSV/Excel de la liste générale."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings


class CsvImportView(QWidget):
    """Importation de personnels depuis un fichier CSV ou Excel."""

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._import_result = None

        title = QLabel("Import CSV / Excel — Liste générale")
        title.setObjectName("dashTitle")

        subtitle = QLabel(
            "Importez des personnels depuis un fichier CSV (séparé par ; ou ,) "
            "ou Excel (.xlsx). Les colonnes sont détectées automatiquement. "
            "Uniquement disponible en mode SQLite."
        )
        subtitle.setObjectName("dashSubtitle")
        subtitle.setWordWrap(True)

        # ── Sélection du fichier ──────────────────────────────────────────
        self._lbl_file = QLabel("Aucun fichier sélectionné.")
        self._lbl_file.setObjectName("dashSubtitle")
        self._lbl_file.setWordWrap(True)

        btn_browse = QPushButton("Choisir un fichier…")
        btn_browse.clicked.connect(self._browse)

        btn_template = QPushButton("Télécharger un modèle CSV…")
        btn_template.setToolTip(
            "Génère un fichier CSV vide avec toutes les colonnes attendues."
        )
        btn_template.clicked.connect(self._download_template)

        file_row = QHBoxLayout()
        file_row.setSpacing(8)
        file_row.addWidget(btn_browse)
        file_row.addWidget(btn_template)
        file_row.addWidget(self._lbl_file, stretch=1)

        # ── Options ───────────────────────────────────────────────────────
        self._chk_skip_dup = QCheckBox("Ignorer les doublons (même nom exact déjà présent)")
        self._chk_skip_dup.setChecked(True)

        grp_opts = QGroupBox("Options d'import")
        opts_lay = QVBoxLayout(grp_opts)
        opts_lay.addWidget(self._chk_skip_dup)

        # ── Aperçu ────────────────────────────────────────────────────────
        self._lbl_preview_title = QLabel("Aperçu des données")
        self._lbl_preview_title.setObjectName("dashSubtitle")

        self._tbl_preview = QTableWidget(0, 0)
        self._tbl_preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl_preview.setAlternatingRowColors(True)
        self._tbl_preview.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl_preview.verticalHeader().setVisible(False)

        self._lbl_mapping = QLabel()
        self._lbl_mapping.setObjectName("dashSubtitle")
        self._lbl_mapping.setWordWrap(True)

        self._lbl_stats = QLabel()
        self._lbl_stats.setObjectName("dashSubtitle")

        # ── Actions ───────────────────────────────────────────────────────
        self._btn_import = QPushButton("Importer dans la base SQLite")
        self._btn_import.setEnabled(False)
        self._btn_import.clicked.connect(self._do_import)

        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(8)
        btn_bar.addWidget(self._btn_import)
        btn_bar.addStretch()
        btn_bar.addWidget(self._lbl_stats)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(subtitle)
        lay.addLayout(file_row)
        lay.addWidget(grp_opts)
        lay.addWidget(self._lbl_mapping)
        lay.addWidget(self._lbl_preview_title)
        lay.addWidget(self._tbl_preview, stretch=1)
        lay.addLayout(btn_bar)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    # ── Sélection du fichier ─────────────────────────────────────────────

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Ouvrir un fichier à importer",
            "", "Fichiers CSV/Excel (*.csv *.txt *.xlsx *.xls *.xlsm)",
        )
        if not path:
            return
        self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        from sme_gend.services.csv_excel_import_service import CsvExcelImportService
        from sme_gend.ui.bg_task import run_with_progress

        svc = CsvExcelImportService()

        def do_parse():
            return svc.parse_file(path)

        result = run_with_progress(
            self, "Import", f"Lecture de {path.name}…", do_parse
        )
        if result is None:
            return

        self._import_result = result
        self._lbl_file.setText(f"<b>{path.name}</b> — {len(result.rows)} ligne(s) lue(s)")

        # Affichage du mapping
        mapped_parts = [
            f"<b>{col}</b> → {field}"
            for col, field in result.column_map.items()
        ]
        mapping_txt = "Colonnes mappées : " + ", ".join(mapped_parts) if mapped_parts else "Aucune colonne reconnue."
        if result.unmapped_columns:
            mapping_txt += f" | Non reconnues : {', '.join(result.unmapped_columns[:8])}"
        self._lbl_mapping.setText(mapping_txt)
        self._lbl_mapping.setTextFormat(Qt.TextFormat.RichText)

        # Aperçu (30 premières lignes valides)
        valid = result.valid_rows
        self._fill_preview(valid[:30])

        n_valid = len(valid)
        n_errors = len(result.errors)
        self._lbl_stats.setText(
            f"{n_valid} ligne(s) valide(s){f' · {n_errors} erreur(s)' if n_errors else ''}"
        )
        self._btn_import.setEnabled(n_valid > 0 and self._settings.use_sqlite_liste_generale)
        if not self._settings.use_sqlite_liste_generale:
            self._btn_import.setToolTip("L'import nécessite le mode SQLite (activé dans les paramètres).")

        if result.errors:
            QMessageBox.warning(
                self, "Avertissements de parsing",
                f"{len(result.errors)} problème(s) :\n" + "\n".join(result.errors[:15])
            )

    def _fill_preview(self, rows) -> None:
        if not rows:
            self._tbl_preview.setRowCount(0)
            self._tbl_preview.setColumnCount(0)
            return

        from sme_gend.services.csv_excel_import_service import _FIELD_LABELS
        fields = [
            "noms", "grade", "structure", "date_naissance",
            "date_service", "matricule_militaire", "matricule_solde",
            "position_admin", "fonction",
        ]
        headers = [_FIELD_LABELS.get(f, f) for f in fields]

        self._tbl_preview.setColumnCount(len(fields))
        self._tbl_preview.setHorizontalHeaderLabels(headers)
        hdr = self._tbl_preview.horizontalHeader()
        for i in range(len(fields)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._tbl_preview.setRowCount(0)
        for r in rows:
            row = self._tbl_preview.rowCount()
            self._tbl_preview.insertRow(row)
            for ci, f in enumerate(fields):
                val = getattr(r, f, None) or ""
                it = QTableWidgetItem(str(val))
                it.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                self._tbl_preview.setItem(row, ci, it)

    # ── Import ────────────────────────────────────────────────────────────

    def _do_import(self) -> None:
        if self._import_result is None:
            return
        if not self._settings.use_sqlite_liste_generale:
            QMessageBox.warning(
                self, "Import",
                "L'import CSV/Excel nécessite le mode SQLite.\n"
                "Activez-le dans Paramètres & données."
            )
            return

        from sme_gend.services.csv_excel_import_service import CsvExcelImportService
        from sme_gend.ui.bg_task import run_with_progress

        valid_rows = self._import_result.valid_rows
        skip_dup = self._chk_skip_dup.isChecked()
        svc = CsvExcelImportService()
        settings = self._settings

        def do_import():
            return svc.import_into_sqlite(valid_rows, settings, skip_duplicates=skip_dup)

        result = run_with_progress(
            self, "Import", f"Import de {len(valid_rows)} ligne(s)…", do_import
        )
        if result is None:
            return

        inserted, skipped, errors = result
        msg = f"{inserted} personne(s) importée(s)."
        if skipped:
            msg += f"\n{skipped} doublon(s) ignoré(s)."
        if errors:
            msg += f"\n{len(errors)} erreur(s) :\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Import terminé", msg)

        if inserted > 0:
            self._import_result = None
            self._tbl_preview.setRowCount(0)
            self._lbl_file.setText("Import effectué — rechargez la liste générale.")
            self._btn_import.setEnabled(False)
            self._lbl_stats.setText(f"{inserted} importé(s)")

    # ── Modèle CSV ────────────────────────────────────────────────────────

    def _download_template(self) -> None:
        dest, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le modèle CSV",
            "modele_import_personnel.csv",
            "Fichiers CSV (*.csv)",
        )
        if not dest:
            return
        from sme_gend.services.csv_excel_import_service import _FIELD_LABELS
        headers = list(_FIELD_LABELS.keys())
        import csv as _csv
        with open(dest, "w", newline="", encoding="utf-8-sig") as f:
            writer = _csv.writer(f, delimiter=";")
            writer.writerow(headers)
            writer.writerow(["DUPONT Jean-Paul", "Capitaine", "GROUPEMENT BRAZZAVILLE",
                             "15/03/1980", "01/01/2005", "GM12345", "SOL98765",
                             "Présent", "Chef de bureau", "GBZ", "Cavalerie", "", "", "", "", "", ""])
        QMessageBox.information(self, "Modèle CSV", f"Modèle enregistré :\n{dest}")
