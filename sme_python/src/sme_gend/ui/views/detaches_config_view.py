"""Référentiel — configuration du fichier Excel DETACHES (sigles, titres, ordre)."""

from __future__ import annotations

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
    """Édition du JSON `data/detaches_config.json`."""

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
            "Le tableau est rempli à partir de ce fichier JSON : au départ il est <b>vide</b>. "
            "Utilisez <b>Importer les sigles depuis Access</b> pour créer une ligne par "
            "<b>subdivision arme</b> distincte parmi les personnels en position <b>Détaché</b>, "
            "puis complétez les titres Excel. "
            "Chaque <b>sigle</b> doit correspondre à la subdivision arme (normalisée en majuscules). "
            "L’<b>ordre</b> classe les blocs (sauf le sigle « présidence » défini ci-dessus, placé en tête)."
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

        self._btn_reload = QPushButton("Recharger depuis le fichier")
        self._btn_reload.clicked.connect(self._reload)
        self._btn_import = QPushButton("Importer les sigles depuis Access (détachés)")
        self._btn_import.setToolTip(
            "Ajoute les lignes manquantes : une par valeur distincte de « subdivision arme » "
            "pour les personnes dont la position administrative est « Détaché »."
        )
        self._btn_import.clicked.connect(self._import_sigles_access)
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
        self._auto_synced = False  # détecte le premier affichage

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._auto_synced:
            self._auto_synced = True
            self._silent_sync_sigles()

    def _silent_sync_sigles(self) -> None:
        """
        Ajoute silencieusement les sigles détachés présents en base mais absents de la config.
        Recharge le tableau si des sigles ont été ajoutés.
        """
        try:
            _, added = self._svc.sync_missing_sigles()
        except Exception:
            return
        if not added:
            return
        self._load_table_from_disk()
        QMessageBox.information(
            self,
            "Sigles ajoutés automatiquement",
            f"{len(added)} nouveau(x) sigle(s) détaché(s) ajouté(s) depuis la base :\n"
            + "\n".join(f"  • {s}" for s in added)
            + "\n\nComplétez les titres officiels puis enregistrez.",
        )

    def _touch_dirty(self) -> None:
        if not self._suspend_dirty:
            self._dirty = True

    def _on_table_item_changed(self, _item: QTableWidgetItem | None) -> None:
        self._touch_dirty()

    def _import_sigles_access(self) -> None:
        if not confirm_yes_no(
            self,
            "Importer depuis Access",
            "Importer les sigles distincts des personnels « Détaché » depuis la base ?\n"
            "Les lignes déjà présentes (même sigle) ne seront pas dupliquées.",
            default_no=True,
        ):
            return
        try:
            ds = DetachesService(self._svc.settings)
            pairs = ds.distinct_sigles_with_counts()
        except Exception as e:
            QMessageBox.critical(self, "Base Access", str(e))
            return
        if not pairs:
            QMessageBox.information(
                self,
                "Aucun sigle",
                "Aucune ligne « Détaché » avec subdivision arme dans la base, "
                "ou aucun personnel en position administrative « Détaché ».\n\n"
                "Vérifiez la liste générale et le champ « subdivision arme ».",
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
                f"{added} ligne(s) ajoutée(s) ({len(pairs)} sigle(s) distinct(s) dans Access). "
                "Complétez les titres officiels puis enregistrez.",
            )

    def _reload(self) -> None:
        if self._dirty:
            if not confirm_yes_no(
                self,
                "Recharger",
                "Des modifications non enregistrées seront perdues.\n"
                "Recharger la configuration depuis le fichier disque ?",
                default_no=True,
            ):
                return
        elif not confirm_yes_no(
            self,
            "Recharger",
            "Recharger la configuration depuis le fichier disque ?\n"
            "(Utile si le fichier a été modifié ailleurs.)",
            default_no=True,
        ):
            return
        self._load_table_from_disk()

    def _load_table_from_disk(self) -> None:
        cfg = self._svc.load()
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
        ordered = sorted(cfg.structures, key=lambda s: (s.ordre, s.sigle))
        for s in ordered:
            r = self._table.rowCount()
            self._table.insertRow(r)
            titres = (s.titres_lignes + ["", "", ""])[:3]
            self._table.setItem(r, 0, QTableWidgetItem(str(s.ordre)))
            self._table.setItem(r, 1, QTableWidgetItem(s.sigle))
            for c in range(3):
                self._table.setItem(r, 2 + c, QTableWidgetItem(titres[c]))
        self._table.blockSignals(False)
        self._title_feuille.blockSignals(False)
        self._lieu.blockSignals(False)
        self._non_ref.blockSignals(False)
        self._presidence.blockSignals(False)
        self._suspend_dirty = False
        self._dirty = False

    def _gather_options(self) -> DetachesFileOptions:
        return DetachesFileOptions(
            titre_ligne_feuille=self._title_feuille.text().strip() or DetachesFileOptions.titre_ligne_feuille,
            lieu_defaut=self._lieu.text().strip() or DetachesFileOptions.lieu_defaut,
            libelle_bloc_non_reference=self._non_ref.text().strip() or DetachesFileOptions.libelle_bloc_non_reference,
            sigle_presidence=self._presidence.text().strip().upper(),
        )

    def _gather_structures(self) -> list[DetachesStructureEntry]:
        out: list[DetachesStructureEntry] = []
        for r in range(self._table.rowCount()):
            ord_s = (self._table.item(r, 0).text() if self._table.item(r, 0) else "").strip()
            sig = (self._table.item(r, 1).text() if self._table.item(r, 1) else "").strip()
            if not sig:
                continue
            try:
                ord_i = int(ord_s) if ord_s else r + 1
            except ValueError:
                ord_i = r + 1
            titres: list[str] = []
            for c in range(3):
                it = self._table.item(r, 2 + c)
                t = (it.text() if it else "").strip()
                if t:
                    titres.append(t)
            out.append(DetachesStructureEntry(sigle=sig, ordre=ord_i, titres_lignes=titres))
        return out

    def _save(self) -> None:
        if not confirm_yes_no(
            self,
            "Enregistrer",
            "Enregistrer la configuration dans le fichier JSON sur le disque ?",
            default_no=True,
        ):
            return
        structs = self._gather_structures()
        sigles = [s.sigle for s in structs]
        if len(sigles) != len(set(sigles)):
            QMessageBox.warning(self, "Configuration", "Chaque sigle doit être unique.")
            return
        cfg = DetachesFileConfig(
            version=1,
            options=self._gather_options(),
            structures=structs,
        )
        try:
            self._svc.save(cfg)
        except OSError as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        self._dirty = False
        QMessageBox.information(
            self,
            "Enregistré",
            f"Configuration enregistrée :\n{self._svc.path()}",
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
        for c in range(3):
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
        for c in range(5):
            a = self._table.takeItem(row, c)
            b = self._table.takeItem(nr, c)
            self._table.setItem(row, c, b)
            self._table.setItem(nr, c, a)
        self._table.setCurrentCell(nr, self._table.currentColumn())
        self._dirty = True
