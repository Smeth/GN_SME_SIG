"""Éditeur de mise en page : ordre des sections (glisser-déposer), titres, options."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.detaches_file_config import DetachesStructureEntry, _norm_sigle
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.ui.confirm_dialogs import confirm_yes_no


def _parse_int_opt(s: str) -> int | None:
    s = (s or "").strip()
    if not s:
        return None
    return int(s)


class LayoutEditorView(QWidget):
    """Configuration dynamique des sections de la liste des présents."""

    def __init__(
        self,
        layout_service: LayoutConfigService,
        detaches_service: DetachesConfigService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._svc = layout_service
        self._detaches_svc = detaches_service
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

        self._sub_list = QListWidget()
        self._sub_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._sub_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._sub_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._sub_list.setMinimumHeight(220)
        self._sub_list.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self._sub_filter = QLineEdit()
        self._sub_filter.setPlaceholderText("Filtrer les libellés (recherche)…")
        self._sub_filter.setClearButtonEnabled(True)
        self._sub_filter.textChanged.connect(self._apply_sub_filter)

        self._sub_count = QLabel()
        self._sub_count.setObjectName("pageSubtitle")

        self._sub_help = QLabel(
            "<b>Ordre des SubArme</b> : glisser-déposer, ou boutons <b>Monter</b> / <b>Descendre</b> "
            "(raccourcis <b>Ctrl+↑</b> / <b>Ctrl+↓</b> lorsque la liste a le focus). "
            "Utilisez le <b>filtre</b> pour retrouver une ligne dans les longues listes. "
            "<b>Trier A→Z</b> : ordre alphabétique ; « (Sans subdivision) » reste en dernier si présente. "
            "Une ligne = un libellé comme en base. Liste vide = ordre alphabétique automatique au rendu."
        )
        self._sub_help.setWordWrap(True)
        self._sub_help.setTextFormat(Qt.TextFormat.RichText)

        self._btn_sub_up = QPushButton("Monter")
        self._btn_sub_up.setToolTip("Déplacer la ligne sélectionnée vers le haut (Ctrl+↑)")
        self._btn_sub_up.clicked.connect(lambda: self._move_sub_item(-1))

        self._btn_sub_down = QPushButton("Descendre")
        self._btn_sub_down.setToolTip("Déplacer la ligne sélectionnée vers le bas (Ctrl+↓)")
        self._btn_sub_down.clicked.connect(lambda: self._move_sub_item(1))

        self._btn_sub_sort = QPushButton("Trier A→Z")
        self._btn_sub_sort.setToolTip(
            "Tri alphabétique de toutes les lignes ; « (Sans subdivision) » en fin de liste."
        )
        self._btn_sub_sort.clicked.connect(self._sort_sub_list_alpha)

        sc_up = QShortcut(QKeySequence("Ctrl+Up"), self._sub_list)
        sc_up.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_up.activated.connect(lambda: self._move_sub_item(-1))
        sc_dn = QShortcut(QKeySequence("Ctrl+Down"), self._sub_list)
        sc_dn.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc_dn.activated.connect(lambda: self._move_sub_item(1))

        self._btn_fill_sub = QPushButton("Remplir depuis la base…")
        self._btn_fill_sub.setToolTip(
            "Réinitialise la liste avec les SubArme distincts pour cette structure (tri alphabétique) ; "
            "vous pouvez ensuite réordonner par glisser-déposer."
        )
        self._btn_fill_sub.clicked.connect(self._fill_sub_from_db)

        self._btn_sub_add = QPushButton("Ajouter une ligne…")
        self._btn_sub_add.clicked.connect(self._add_sub_line)

        self._btn_sub_del = QPushButton("Supprimer la ligne")
        self._btn_sub_del.clicked.connect(self._remove_sub_line)

        self._btn_sub_rename_db = QPushButton("Renommer en base (SubArme)…")
        self._btn_sub_rename_db.setToolTip(
            "Met à jour la colonne SubArme dans ListeGenerale pour toutes les lignes "
            "de cette structure ayant ce libellé, puis la mise en page JSON."
        )
        self._btn_sub_rename_db.clicked.connect(self._rename_subarme_in_db)

        self._btn_struct_db = QPushButton("Code & référentiel (base)…")
        self._btn_struct_db.setToolTip(
            "Désignation et ordre (TabStructure), et optionnellement renommer le code "
            "(TabStructure + ListeGenerale + cette configuration)."
        )
        self._btn_struct_db.clicked.connect(self._edit_structure_in_db)

        self._sub_block = QGroupBox("Ordre des subdivisions d’arme (cette structure)")
        sub_lay = QVBoxLayout(self._sub_block)
        sub_lay.addWidget(self._sub_help)
        sub_filter_row = QHBoxLayout()
        sub_filter_row.addWidget(QLabel("Filtre :"))
        sub_filter_row.addWidget(self._sub_filter, stretch=1)
        sub_filter_row.addWidget(self._sub_count)
        sub_lay.addLayout(sub_filter_row)
        sub_lay.addWidget(self._sub_list, stretch=1)
        btn_move = QHBoxLayout()
        btn_move.addWidget(self._btn_sub_up)
        btn_move.addWidget(self._btn_sub_down)
        btn_move.addWidget(self._btn_sub_sort)
        btn_move.addStretch()
        sub_lay.addLayout(btn_move)
        btn_sub = QHBoxLayout()
        btn_sub.addWidget(self._btn_fill_sub)
        btn_sub.addWidget(self._btn_sub_add)
        btn_sub.addWidget(self._btn_sub_del)
        btn_sub.addWidget(self._btn_sub_rename_db)
        btn_sub.addStretch()
        sub_lay.addLayout(btn_sub)

        form = QFormLayout()
        row_code = QHBoxLayout()
        row_code.addWidget(self._code, stretch=1)
        row_code.addWidget(self._btn_struct_db)
        form.addRow("Structure (code) :", row_code)
        form.addRow("Titre principal (override) :", self._title_main)
        form.addRow("Sous-titre (override) :", self._title_sub)
        form.addRow(self._grp_sub)
        form.addRow(self._sub_block)

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
            "<b>Structures</b> : glisser-déposer la liste du haut. "
            "<b>Subdivisions</b> : filtre de recherche, boutons Monter / Descendre "
            "(<b>Ctrl+↑</b> / <b>Ctrl+↓</b>), tri <b>A→Z</b>, ou glisser-déposer. "
            "Cliquez <b>Enregistrer le fichier JSON</b> pour appliquer l’ordre des structures "
            "et, pour la section sélectionnée, titres + ordre des subdivisions (prise en compte "
            "immédiate à l’aperçu / export après actualisation). "
            "<b>Appliquer les champs à la section</b> reste utile pour valider sans écrire le disque. "
            "<b>Base Access</b> : « Code & référentiel » et « Renommer en base (SubArme) » — "
            "puis actualisez <b>Liste des présents</b>."
        )
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addWidget(QLabel("<b>Sections (ordre = ordre du document)</b>"))
        lay.addWidget(self._list, stretch=2)
        lay.addWidget(grp, stretch=3)
        lay.addLayout(bar)

        self._populate_list()
        self._update_sub_order_widget_state()
        self._apply_sub_filter()

    def _patch_detaches_sigle(self, old_label: str, new_label: str) -> None:
        """Si un sigle DETACHES correspond à l’ancien libellé, le remplace."""
        if self._detaches_svc is None:
            return
        o = _norm_sigle(old_label)
        n_raw = (new_label or "").strip()
        if not o or not n_raw:
            return
        cfg = self._detaches_svc.load()
        changed = False
        new_list: list[DetachesStructureEntry] = []
        for ent in cfg.structures:
            if ent.sigle == o:
                new_list.append(
                    DetachesStructureEntry(
                        sigle=n_raw,
                        ordre=ent.ordre,
                        titres_lignes=list(ent.titres_lignes),
                    )
                )
                changed = True
            else:
                new_list.append(ent)
        if changed:
            cfg.structures = new_list
            try:
                self._detaches_svc.save(cfg)
            except OSError as e:
                QMessageBox.warning(
                    self,
                    "Config. DETACHES",
                    f"SubArme mis à jour en base, mais enregistrement detaches_config.json échoué :\n{e}",
                )

    def _sub_labels_from_list(self) -> list[str]:
        out: list[str] = []
        for i in range(self._sub_list.count()):
            t = self._sub_list.item(i).text().strip()
            if t:
                out.append(t)
        return out

    def _merge_sub_labels_db_with_preferred_order(
        self, sec: SectionBlock, db_labels: list[str]
    ) -> list[str]:
        """
        Conserve l'ordre courant (liste ou subdivision_order en JSON) pour les libellés
        encore présents en base ; ajoute en fin les nouveaux libellés (tri alphabétique).
        « (Sans subdivision) » est conservé s'il figure dans l'ordre prédéfini.
        """
        db_set = {(x or "").strip() for x in db_labels if (x or "").strip()}
        pref = self._sub_labels_from_list()
        if not pref and sec.subdivision_order:
            pref = list(sec.subdivision_order)
        sans = "(Sans subdivision)"
        out: list[str] = []
        seen: set[str] = set()
        for p in pref:
            p = (p or "").strip()
            if not p:
                continue
            if p == sans:
                if p not in seen:
                    out.append(p)
                    seen.add(p)
                continue
            if p in db_set:
                out.append(p)
                seen.add(p)
        for x in sorted(db_set - seen):
            out.append(x)
        return out

    def _set_sub_list_labels(self, labels: list[str]) -> None:
        self._sub_list.clear()
        for lb in labels:
            s = (lb or "").strip()
            if s:
                self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()

    def _apply_sub_filter(self) -> None:
        needle = self._sub_filter.text().strip().lower()
        n_visible = 0
        for i in range(self._sub_list.count()):
            it = self._sub_list.item(i)
            text = (it.text() if it else "").lower()
            match = not needle or needle in text
            self._sub_list.setRowHidden(i, not match)
            if match:
                n_visible += 1
        total = self._sub_list.count()
        if total == 0:
            self._sub_count.setText("")
        elif needle:
            self._sub_count.setText(f"{n_visible} / {total} visibles")
        else:
            self._sub_count.setText(f"{total} subdivision(s)")

    def _move_sub_item(self, delta: int) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        row = self._sub_list.currentRow()
        if row < 0:
            QMessageBox.information(
                self,
                "Subdivision",
                "Sélectionnez une ligne dans la liste.",
            )
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self._sub_list.count():
            return
        item = self._sub_list.takeItem(row)
        self._sub_list.insertItem(new_row, item)
        self._sub_list.setCurrentRow(new_row)
        self._sub_list.setFocus()
        self._apply_sub_filter()

    def _sort_sub_list_alpha(self) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        labels = self._sub_labels_from_list()
        if len(labels) < 2:
            return
        sans = "(Sans subdivision)"
        rest = [x for x in labels if x != sans]
        sorted_rest = sorted(rest, key=lambda s: str(s).lower())
        out = sorted_rest
        if sans in labels:
            out = sorted_rest + [sans]
        self._set_sub_list_labels(out)

    def _fill_sub_from_db(self) -> None:
        sec = self._current_section()
        if not sec or not self._grp_sub.isChecked():
            return
        code = (sec.structure_code or "").strip()
        if not code:
            QMessageBox.warning(self, "Structure", "Aucun code structure sélectionné.")
            return
        if not confirm_yes_no(
            self,
            "Fusionner avec la base",
            "Lire les libellés SubArme distincts dans Access pour cette structure et "
            "mettre à jour la liste ?\n\n"
            "L’ordre des lignes que vous avez défini (ou enregistré dans le JSON) est "
            "conservé pour les libellés encore présents en base. Les nouveaux libellés "
            "sont ajoutés à la fin (ordre alphabétique).",
            default_no=True,
        ):
            return
        try:
            labels = self._svc.subarme_labels_for_structure(code)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        merged = self._merge_sub_labels_db_with_preferred_order(sec, labels)
        if not merged and not labels:
            QMessageBox.information(
                self,
                "Subdivisions",
                f"Aucune valeur SubArme non vide trouvée pour la structure « {code} ».",
            )
            self._set_sub_list_labels([])
            return
        self._set_sub_list_labels(merged)

    def _add_sub_line(self) -> None:
        if not self._current_section() or not self._grp_sub.isChecked():
            return
        text, ok = QInputDialog.getText(
            self,
            "Subdivision",
            "Libellé (exactement comme en base, ou « (Sans subdivision) ») :",
        )
        if not ok:
            return
        s = (text or "").strip()
        if not s:
            return
        if not confirm_yes_no(
            self,
            "Ajouter une subdivision",
            f"Ajouter la ligne « {s} » à la liste locale (ordre d'affichage) ?",
            default_no=True,
        ):
            return
        self._sub_list.addItem(QListWidgetItem(s))
        self._apply_sub_filter()

    def _remove_sub_line(self) -> None:
        row = self._sub_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Subdivision", "Sélectionnez une ligne à supprimer.")
            return
        it = self._sub_list.item(row)
        label = it.text().strip() if it else ""
        if not confirm_yes_no(
            self,
            "Supprimer la ligne",
            f"Retirer « {label} » de la liste locale ?\n\n"
            "(Enregistrez le fichier JSON après « Appliquer » pour persister.)",
            default_no=True,
        ):
            return
        self._sub_list.takeItem(row)
        self._apply_sub_filter()

    def _rename_subarme_in_db(self) -> None:
        sec = self._current_section()
        if not sec or not self._grp_sub.isChecked():
            return
        item = self._sub_list.currentItem()
        if not item:
            QMessageBox.information(
                self,
                "Subdivision",
                "Sélectionnez une ligne dans la liste des subdivisions.",
            )
            return
        old_label = item.text().strip()
        if not old_label:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Renommer SubArme en base")
        le_old = QLineEdit(old_label)
        le_old.setReadOnly(True)
        le_new = QLineEdit(old_label)
        form = QFormLayout()
        form.addRow("Libellé actuel :", le_old)
        form.addRow("Nouveau libellé :", le_new)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addLayout(form)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_label = le_new.text().strip()
        if not new_label or new_label == old_label:
            return
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Mettre à jour ListeGenerale : SubArme « {old_label} » → « {new_label} » "
            f"pour la structure « {sec.structure_code.strip()} » ?\n\n"
            "Les lignes du JSON de mise en page seront alignées et le fichier enregistré.",
            default_no=True,
        ):
            return
        try:
            n, self._cfg = self._svc.rename_subarme_in_database(
                self._cfg, sec.structure_code, old_label, new_label
            )
            self._svc.save(self._cfg)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return
        self._patch_detaches_sigle(old_label, new_label)
        item.setText(new_label)
        self._apply_sub_filter()
        QMessageBox.information(
            self,
            "OK",
            f"{n} ligne(s) mise(s) à jour dans ListeGenerale.\n"
            "Actualisez la page Liste des présents pour l’aperçu.",
        )

    def _edit_structure_in_db(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        code_cur = sec.structure_code.strip()
        row = self._svc.structure_row(code_cur)
        if row is None:
            QMessageBox.warning(
                self,
                "Référentiel",
                f"Aucune entrée dans TabStructure pour le code « {code_cur} ».\n\n"
                "Ajoutez ou corrigez la structure depuis le menu Référentiels → Structures, "
                "puis revenez ici.",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Structure — base Access")
        le_code_new = QLineEdit(code_cur)
        le_des = QLineEdit(row.designation if row else "")
        le_ordre = QLineEdit("" if row is None or row.ordre_tri is None else str(row.ordre_tri))
        form = QFormLayout()
        form.addRow(
            "Nouveau code (vide = inchangé) :",
            le_code_new,
        )
        form.addRow("Désignation (TabStructure) :", le_des)
        form.addRow("Ordre tri (TabStructure) :", le_ordre)
        hint = QLabel(
            "Si vous changez le <b>code</b>, toutes les lignes ListeGenerale concernées sont mises à jour, "
            "ainsi que TabStructure et cette configuration JSON."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addWidget(hint)
        v.addLayout(form)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_code = le_code_new.text().strip() or code_cur
        des = le_des.text().strip()
        ordre = _parse_int_opt(le_ordre.text())
        if not des:
            QMessageBox.warning(self, "Structure", "La désignation ne peut pas être vide.")
            return

        if not confirm_yes_no(
            self,
            "Confirmer les modifications",
            "Enregistrer dans Access (TabStructure, ListeGenerale si changement de code) "
            "et sauvegarder le fichier de mise en page JSON ?",
            default_no=True,
        ):
            return

        try:
            if new_code != code_cur:
                self._cfg = self._svc.rename_structure_code(self._cfg, code_cur, new_code)
                code_cur = new_code
            self._svc.update_structure_referentiel(code_cur, des, ordre)
            self._svc.save(self._cfg)
        except Exception as e:
            QMessageBox.critical(self, "Base de données", str(e))
            return

        self._code.setText(code_cur)
        self._populate_list(select_section_id=sec.id)
        QMessageBox.information(
            self,
            "OK",
            "Référentiel et configuration enregistrés.\n"
            "Actualisez la page Liste des présents pour l’aperçu.",
        )

    def _update_sub_order_widget_state(self) -> None:
        sec = self._current_section()
        ok = bool(sec and self._grp_sub.isChecked())
        self._sub_block.setEnabled(ok)

    def _populate_list(self, select_section_id: str | None = None) -> None:
        self._list.clear()
        for sec in sorted(self._cfg.sections, key=lambda s: s.order):
            label = f"{sec.order + 1:02d} — {sec.structure_code}"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, sec.id)
            self._list.addItem(it)
        if not self._list.count():
            return
        row = 0
        if select_section_id:
            for i in range(self._list.count()):
                it = self._list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == select_section_id:
                    row = i
                    break
        self._list.setCurrentRow(row)

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
            self._sub_filter.clear()
            self._apply_sub_filter()
            self._update_sub_order_widget_state()
            return
        self._code.setText(sec.structure_code)
        self._title_main.setText(sec.title_main_override or "")
        self._title_sub.setText(sec.title_sub_override or "")
        self._grp_sub.setChecked(sec.group_by_subdivision)
        self._sub_filter.clear()
        so = sec.subdivision_order
        self._set_sub_list_labels(list(so) if so else [])
        self._update_sub_order_widget_state()

    def _apply_fields(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        if not confirm_yes_no(
            self,
            "Appliquer à la section",
            f"Appliquer les titres et l'ordre des subdivisions à la section « {sec.structure_code.strip()} » "
            "dans la configuration en mémoire ?\n\n"
            "Utilisez « Enregistrer le fichier JSON » pour écrire sur le disque.",
            default_no=True,
        ):
            return
        tm = self._title_main.text().strip()
        ts = self._title_sub.text().strip()
        sec.title_main_override = tm or None
        sec.title_sub_override = ts or None
        sec.group_by_subdivision = self._grp_sub.isChecked()
        if self._grp_sub.isChecked():
            lines = self._sub_labels_from_list()
            sec.subdivision_order = lines if lines else None
        else:
            sec.subdivision_order = None
        QMessageBox.information(self, "Section", "Modifications appliquées (pensez à Enregistrer).")

    def _reorder_sections_from_ui(self) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            sec = next(s for s in self._cfg.sections if s.id == sid)
            sec.order = i

    def _sync_current_section_widgets_to_cfg(self) -> None:
        """Recopie titres, case à cocher et ordre des subdivisions (liste visible) dans la config."""
        sec = self._current_section()
        if not sec:
            return
        tm = self._title_main.text().strip()
        ts = self._title_sub.text().strip()
        sec.title_main_override = tm or None
        sec.title_sub_override = ts or None
        sec.group_by_subdivision = self._grp_sub.isChecked()
        if self._grp_sub.isChecked():
            lines = self._sub_labels_from_list()
            sec.subdivision_order = lines if lines else None
        else:
            sec.subdivision_order = None

    def _save_file(self) -> None:
        if not confirm_yes_no(
            self,
            "Enregistrer",
            "Enregistrer la mise en page dans le fichier JSON sur le disque ?",
            default_no=True,
        ):
            return
        self._sync_current_section_widgets_to_cfg()
        self._reorder_sections_from_ui()
        try:
            self._svc.save(self._cfg)
        except OSError as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        sel_id = self._current_section().id if self._current_section() else None
        self._populate_list(select_section_id=sel_id)
        QMessageBox.information(self, "OK", "Configuration enregistrée.")

    def _reload(self) -> None:
        if not confirm_yes_no(
            self,
            "Recharger",
            "Recharger la configuration depuis le disque ?\n"
            "Les modifications non enregistrées dans le fichier JSON seront perdues.",
            default_no=True,
        ):
            return
        sel_id = self._current_section().id if self._current_section() else None
        self._cfg = self._svc.load()
        self._populate_list(select_section_id=sel_id)

    def _reset_db(self) -> None:
        if not confirm_yes_no(
            self,
            "Régénérer",
            "Reconstruire la liste des sections à partir de TabStructure ?\n"
            "Pensez à avoir enregistré une copie si la configuration actuelle vous convient.",
            default_no=True,
        ):
            return
        self._cfg = self._svc.build_default_from_database()
        self._populate_list()

    def _merge_missing(self) -> None:
        if not confirm_yes_no(
            self,
            "Structures manquantes",
            "Ajouter en fin de liste les structures présentes en base mais absentes de la configuration ?",
            default_no=True,
        ):
            return
        self._reorder_sections_from_ui()
        self._cfg = self._svc.merge_missing_structures(self._cfg)
        self._populate_list()

    def _remove_section(self) -> None:
        sec = self._current_section()
        if not sec:
            return
        if not confirm_yes_no(
            self,
            "Supprimer la section",
            f"Supprimer la section « {sec.structure_code} » de la configuration ?\n"
            "Enregistrez le fichier pour rendre la suppression définitive.",
            default_no=True,
        ):
            return
        self._cfg.sections = [s for s in self._cfg.sections if s.id != sec.id]
        for i, s in enumerate(sorted(self._cfg.sections, key=lambda x: x.order)):
            s.order = i
        self._populate_list()
