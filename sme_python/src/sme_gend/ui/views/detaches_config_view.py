"""Référentiel — configuration du fichier Excel DETACHES (sigles, titres, ordre)."""

from __future__ import annotations
from copy import deepcopy

from sme_gend.ui.shared_config_editor import displayed_config, config_editor_action


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QFormLayout,
)

from sme_gend.models.detaches_file_config import DetachesFileConfig, DetachesFileOptions, DetachesStructureEntry
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.detaches_service import DetachesService
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class DetachesConfigView(QWidget):
    """Édition de la configuration partagée du fichier des détachés."""

    def __init__(self, service: DetachesConfigService, parent=None) -> None:
        super().__init__(parent)
        self._svc = service
        self._dirty = False
        self._suspend_dirty = False

        self._title_feuille = QLineEdit()
        self._lieu = QLineEdit()
        self._non_ref = QLineEdit()
        self._presidence = QLineEdit()
        self._presidence.setPlaceholderText("ex. PR — laisser vide si non utilisé")

        for w in (self._title_feuille, self._lieu, self._non_ref, self._presidence):
            w.textChanged.connect(self._touch_dirty)

        opt_grp = QGroupBox("Options — en-tête et comportement")
        form_opt = QFormLayout(opt_grp)
        form_opt.addRow("Titre ligne 1 de la feuille DETACHE :", self._title_feuille)
        form_opt.addRow("Lieu de détachement par défaut :", self._lieu)
        form_opt.addRow("Libellé du bloc « sigles non configurés » :", self._non_ref)
        form_opt.addRow("Sigle présidence (toujours en tête) :", self._presidence)

        hint = QLabel(
            "Configuration partagée entre les postes. Utilisez <b>Importer les sigles depuis la base</b> "
            "pour une ligne par <b>subdivision arme</b> (périmètre détachés) ; complétez les titres. "
            "Les <b>titres</b> pour chaque sigle sont ceux imprimés dans la feuille <code>DETACHE</code> "
            "(souvent le <b>nom complet</b> de la structure). "
            "Chaque <b>sigle</b> = subdivision arme en base (majuscules). "
            "L’<b>ordre</b> classe les blocs (sauf <b>sigle présidence</b> en tête). "
            "Enregistrez pour que les exports Excel et l’aperçu Détachés reflètent les titres."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["Ordre", "Sigle (subdivision arme)", "Titre ligne 1", "Titre ligne 2 (optionnel)", "Titre ligne 3 (optionnel)"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.itemChanged.connect(self._on_table_item_changed)

        self._btn_reload = QPushButton("Recharger la configuration partagée")
        self._btn_reload.clicked.connect(self._reload)
        self._btn_import = QPushButton("Importer les sigles depuis la base (détachés)")
        self._btn_import.setToolTip(
            "Ajoute les lignes manquantes : une par « subdivision arme » distincte dans le "
            "périmètre détachés (Padmin « Détaché » ou Structure DETACHE)."
        )
        self._btn_import.clicked.connect(self._import_sigles_from_db)
        self._btn_save = QPushButton("Enregistrer")
        self._btn_save.clicked.connect(self._save)
        self._btn_add = QPushButton("Ajouter une ligne")
        self._btn_add.clicked.connect(self._add_row)
        self._btn_del = QPushButton("Supprimer la sélection")
        self._btn_del.clicked.connect(self._del_rows)
        self._btn_up = QPushButton("Monter")
        self._btn_up.clicked.connect(lambda: self._move(-1))
        self._btn_down = QPushButton("Descendre")
        self._btn_down.clicked.connect(lambda: self._move(1))

        bar = QHBoxLayout()
        bar.addWidget(self._btn_reload)
        bar.addWidget(self._btn_import)
        bar.addWidget(self._btn_save)
        bar.addStretch()
        bar2 = QHBoxLayout()
        bar2.addWidget(self._btn_add)
        bar2.addWidget(self._btn_del)
        bar2.addWidget(self._btn_up)
        bar2.addWidget(self._btn_down)
        bar2.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(opt_grp)
        lay.addLayout(bar)
        lay.addLayout(bar2)
        lay.addWidget(self._table, stretch=1)

        self._load_table_from_disk()

    def is_dirty(self) -> bool:
        return self._dirty

    def _touch_dirty(self) -> None:
        if not self._suspend_dirty:
            self._dirty = True

    def _on_table_item_changed(self, _item: QTableWidgetItem | None) -> None:
        self._touch_dirty()

    def _import_sigles_from_db(self) -> None:
        if not confirm_yes_no(
            self,
            "Importer depuis la base",
            "Importer les sigles distincts (subdivision arme) du périmètre détachés depuis la base ?\n"
            "Les lignes déjà présentes (même sigle) ne seront pas dupliquées.",
            default_no=True,
        ):
            return
        try:
            ds = DetachesService(self._svc.settings)
            pairs = ds.distinct_sigles_with_counts()
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        if not pairs:
            QMessageBox.information(
                self,
                "Aucun sigle",
                "Aucune ligne au périmètre détachés avec subdivision arme renseignée dans la base.\n\n"
                "Vérifiez la liste générale (Padmin « Détaché » ou Structure DETACHE, champ « subdivision arme »).",
            )
            return
        existing: set[str] = set()
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 1)
            if it and it.text().strip():
                existing.add(it.text().strip().upper())
        max_ord = 0
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 0)
            if it:
                try:
                    max_ord = max(max_ord, int(it.text()))
                except ValueError:
                    pass
        added = 0
        for sigle, count in pairs:
            if sigle in existing:
                continue
            max_ord += 1
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(str(max_ord)))
            self._table.setItem(row, 1, QTableWidgetItem(sigle))
            self._table.setItem(
                row,
                2,
                QTableWidgetItem(f"— {sigle} — ({count} pers.)"),
            )
            self._table.setItem(row, 3, QTableWidgetItem(""))
            self._table.setItem(row, 4, QTableWidgetItem(""))
            added += 1
        if added > 0:
            self._dirty = True
        if added == 0:
            QMessageBox.information(
                self,
                "Import",
                f"Tous les {len(pairs)} sigle(s) distinct(s) de la base sont déjà présents dans le tableau.",
            )
        else:
            QMessageBox.information(
                self,
                "Import",
                f"{added} ligne(s) ajoutée(s) ({len(pairs)} sigle(s) distinct(s) dans la base). "
                "Complétez les titres officiels puis enregistrez.",
            )

    def _reload(self) -> None:
        if self._dirty:
            if not confirm_yes_no(
                self,
                "Recharger",
                "Des modifications non enregistrées seront perdues.\n"
                "Recharger la configuration partagée ?",
                default_no=True,
            ):
                return
        elif not confirm_yes_no(
            self,
            "Recharger",
            "Recharger la configuration partagée ?\n"
            "(Utile si la configuration a été modifiée sur un autre poste.)",
            default_no=True,
        ):
            return
        self._load_table_from_disk()

    @config_editor_action
    def _load_table_from_disk(self) -> None:
        cfg = self._svc.load()
        self._loaded_config = cfg
        o = cfg.options
        self._suspend_dirty = True
        self._title_feuille.blockSignals(True)
        self._lieu.blockSignals(True)
        self._non_ref.blockSignals(True)
        self._presidence.blockSignals(True)
        self._table.blockSignals(True)
        self._title_feuille.setText(o.titre_ligne_feuille)
        self._lieu.setText(o.lieu_defaut)
        self._non_ref.setText(o.libelle_bloc_non_reference)
        self._presidence.setText(o.sigle_presidence)

        self._table.setRowCount(0)
        title_count = max(3, max((len(s.titres_lignes) for s in cfg.structures), default=0))
        self._table.setColumnCount(2 + title_count)
        self._table.setHorizontalHeaderLabels(
            ["Ordre", "Sigle (subdivision arme)"]
            + [f"Titre ligne {i + 1}" for i in range(title_count)]
        )
        ordered = sorted(cfg.structures, key=lambda s: (s.ordre, s.sigle))
        for s in ordered:
            r = self._table.rowCount()
            self._table.insertRow(r)
            titres = s.titres_lignes + [""] * title_count
            self._table.setItem(r, 0, QTableWidgetItem(str(s.ordre)))
            self._table.setItem(r, 1, QTableWidgetItem(s.sigle))
            self._table.item(r, 0).setData(Qt.ItemDataRole.UserRole, deepcopy(s))
            for c in range(title_count):
                self._table.setItem(r, 2 + c, QTableWidgetItem(titres[c]))
        self._table.blockSignals(False)
        self._title_feuille.blockSignals(False)
        self._lieu.blockSignals(False)
        self._non_ref.blockSignals(False)
        self._presidence.blockSignals(False)
        self._suspend_dirty = False
        self._dirty = False

    def _gather_options(self) -> DetachesFileOptions:
        options = deepcopy(self._loaded_config.options)
        updated = DetachesFileOptions(
            titre_ligne_feuille=self._title_feuille.text().strip() or DetachesFileOptions.titre_ligne_feuille,
            lieu_defaut=self._lieu.text().strip() or DetachesFileOptions.lieu_defaut,
            libelle_bloc_non_reference=self._non_ref.text().strip() or DetachesFileOptions.libelle_bloc_non_reference,
            sigle_presidence=self._presidence.text().strip().upper(),
        )
        for name in updated.to_dict():
            setattr(options, name, getattr(updated, name))
        return options

    def _gather_structures(self) -> list[DetachesStructureEntry]:
        out: list[DetachesStructureEntry] = []
        for r in range(self._table.rowCount()):
            ord_s = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            sig = (self._table.item(r, 1).text() if self._table.item(r, 1) else "").strip()
            if not sig:
                raise ValueError(f"Ligne {r + 1} : renseignez le sigle ou supprimez la ligne avant d'enregistrer.")
            try:
                ord_i = int(ord_s) if ord_s else r + 1
            except ValueError as exc:
                raise ValueError(f"Ligne {r + 1} : l'ordre doit être un nombre entier.") from exc
            titres: list[str] = []
            for c in range(self._table.columnCount() - 2):
                it = self._table.item(r, 2 + c)
                t = (it.text() if it else "").strip()
                if t:
                    titres.append(t)
            original = self._table.item(r, 0)
            entry = original.data(Qt.ItemDataRole.UserRole) if original else None
            entry = deepcopy(entry) if isinstance(entry, DetachesStructureEntry) else DetachesStructureEntry(sigle=sig, ordre=ord_i)
            entry.sigle = sig.strip().upper()
            entry.ordre = ord_i
            entry.titres_lignes = titres
            out.append(entry)
        return out

    @config_editor_action
    def _save(self) -> None:
        if not confirm_yes_no(
            self,
            "Enregistrer",
            "Enregistrer cette configuration partagée pour tous les postes ?",
            default_no=True,
        ):
            return
        structs = self._gather_structures()
        sigles = [s.sigle for s in structs]
        if len(sigles) != len(set(sigles)):
            QMessageBox.warning(self, "Configuration", "Chaque sigle doit être unique.")
            return
        cfg = displayed_config(self)
        cfg.options = self._gather_options()
        cfg.structures = structs
        self._svc.save(cfg)
        self._loaded_config = cfg
        self._dirty = False
        QMessageBox.information(
            self,
            "Enregistré",
            "Configuration partagée enregistrée.",
        )

    def _add_row(self) -> None:
        if not confirm_yes_no(
            self,
            "Ajouter une ligne",
            "Ajouter une ligne vide au tableau (nouveau sigle à saisir) ?",
            default_no=True,
        ):
            return
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(str(r + 1)))
        self._table.setItem(r, 1, QTableWidgetItem(""))
        for c in range(self._table.columnCount() - 2):
            self._table.setItem(r, 2 + c, QTableWidgetItem(""))
        self._dirty = True

    def _del_rows(self) -> None:
        rows = sorted({i.row() for i in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "Suppression", "Sélectionnez une ou plusieurs lignes.")
            return
        n = len(rows)
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer {n} ligne(s) du tableau ?\nCette action est irréversible avant enregistrement.",
            default_no=True,
        ):
            return
        for r in rows:
            self._table.removeRow(r)
        self._dirty = True

    def _move(self, delta: int) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        nr = row + delta
        if nr < 0 or nr >= self._table.rowCount():
            return
        for c in range(self._table.columnCount()):
            a = self._table.takeItem(row, c)
            b = self._table.takeItem(nr, c)
            self._table.setItem(row, c, b)
            self._table.setItem(nr, c, a)
        self._table.setCurrentCell(nr, self._table.currentColumn())
        self._dirty = True
