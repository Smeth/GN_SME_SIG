"""Hiérarchie U→D→T — arbre éditable (ref_subdivision_noeud)."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from sme_gend.services.search_text import normalize_search_text

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.services.subdiv_hierarchy_service import SubdivHierarchyService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.responsive_panels import WrapLayout, ResponsiveSplitter, scroll_panel
from sme_gend.ui.view_lifecycle import ViewDataState

_ROLE_ID = Qt.ItemDataRole.UserRole
_ROLE_LEVEL = Qt.ItemDataRole.UserRole + 1


class _NodeDialog(QDialog):
    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        code: str = "",
        libelle: str = "",
        pri_out: str = "",
        code_readonly: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        form = QFormLayout()
        self._code = QLineEdit(code)
        self._libelle = QLineEdit(libelle)
        self._pri = QLineEdit(pri_out)
        self._code.setPlaceholderText("Code court (ex. ESR)")
        self._libelle.setPlaceholderText("Libellé affiché")
        self._pri.setPlaceholderText("Priorité (optionnel)")
        if code_readonly:
            self._code.setReadOnly(True)
        form.addRow("Code *", self._code)
        form.addRow("Libellé *", self._libelle)
        form.addRow("Priorité sortie", self._pri)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def accept(self) -> None:
        from sme_gend.repositories.mysql_subdiv_hierarchy_repository import _values
        try:
            _values(*self.values())
        except (ValueError,OverflowError) as error:
            QMessageBox.warning(self,"Hierarchie",str(error))
            return
        super().accept()

    def values(self) -> tuple[str, str, float | None]:
        pri_s = self._pri.text().strip()
        pri: float | None = None
        if pri_s:
            pri = float(pri_s.replace(",", "."))
        return self._code.text().strip(), self._libelle.text().strip(), pri


class SubdivisionsHierarchyView(QWidget):
    """Arbre Secun → Secdeux → Sectrois avec CRUD."""

    def __init__(self, referentiel: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._ref = referentiel
        self._svc = SubdivHierarchyService(referentiel.settings)
        self._roots: list[SubdivHierarchyNode] = []
        self._snapshot = None
        self._node_drafts = {}
        self._expanded_states = {}
        self._selected_id = None
        self._view_data = ViewDataState(stale=True)

        title = QLabel("Hiérarchie subdivisions — U → D → T")
        title.setObjectName("dashPageTitle")
        sub = QLabel(
            "Arbre référentiel hérité des tables <b>TabSecun / TabSecdeux / TabSectrois</b>. "
            "Les feuilles (niveau T) servent à rattacher les subdivisions d'arme par structure "
            "dans l'organigramme. Renommer cette nomenclature ne renomme ni les subdivisions "
            "des structures, ni les dossiers ou leurs historiques. Les nœuds liés à des affiliations ne peuvent pas être supprimés."
        )
        sub.setObjectName("dashSubtitle")
        sub.setWordWrap(True)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Filtrer code ou libellé…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._rebuild_tree)

        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.clicked.connect(lambda: self.reload(force=True))

        bar = QHBoxLayout()
        bar.addWidget(self._btn_refresh)
        bar.addWidget(self._search, stretch=1)

        self._tree = QTreeWidget()
        self._tree.setObjectName("organigramTree")
        self._tree.setHeaderLabels(["Code", "Libellé", "Priorité"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(True)
        self._tree.setColumnWidth(0, 120)
        self._tree.setColumnWidth(1, 360)
        self._tree.itemSelectionChanged.connect(self._update_detail)

        self._detail = QFrame()
        self._detail.setObjectName("organigramDetail")
        det_lay = QVBoxLayout(self._detail)
        det_lay.setContentsMargins(16, 14, 16, 14)
        self._detail_title = QLabel("Sélectionnez un nœud")
        self._detail_title.setObjectName("dashKpiCardTitle")
        self._detail_title.setWordWrap(True)
        tf = QFont()
        tf.setPointSize(12)
        tf.setWeight(QFont.Weight.DemiBold)
        self._detail_title.setFont(tf)
        self._detail_body = QLabel("—")
        self._detail_body.setWordWrap(True)
        self._detail_body.setTextFormat(Qt.TextFormat.RichText)
        det_lay.addWidget(self._detail_title)
        det_lay.addWidget(self._detail_body)
        det_lay.addStretch()

        split = ResponsiveSplitter()
        split.addWidget(self._tree)
        split.addWidget(scroll_panel(self._detail))
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)

        tool = WrapLayout()
        self._btn_add_u = QPushButton("Nouveau U…")
        self._btn_add_u.clicked.connect(self._on_add_u)
        self._btn_add_child = QPushButton("Ajouter enfant…")
        self._btn_add_child.clicked.connect(self._on_add_child)
        self._btn_edit = QPushButton("Modifier…")
        self._btn_edit.clicked.connect(self._on_edit)
        self._btn_propagate = QPushButton("Créer RSA (structures concernées)…")
        self._btn_propagate.setToolTip(
            "Pour une feuille T : crée ref_subdivision_arme sur chaque structure "
            "où la carrière porte déjà ce libellé SubArme."
        )
        self._btn_propagate.clicked.connect(self._on_propagate_rsa)
        self._btn_del = QPushButton("Supprimer")
        self._btn_del.clicked.connect(self._on_delete)
        tool.addWidget(self._btn_add_u)
        tool.addWidget(self._btn_add_child)
        tool.addWidget(self._btn_edit)
        tool.addWidget(self._btn_propagate)
        tool.addWidget(self._btn_del)

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addLayout(bar)
        lay.addLayout(tool)
        lay.addWidget(split, stretch=1)

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load():
            self.reload()

    def apply_runtime_after_config_change(self) -> None:
        self._snapshot = None
        self._svc.rebind_settings(self._ref.settings)
        self._view_data.mark_stale()
        if self.isVisible():
            self.reload()

    def reload(self, *, force: bool = False) -> None:
        if not force and not self._view_data.needs_load():
            return
        try:
            roots = run_reference_task(self,"Référentiel",lambda: self._svc.fetch_tree())
            self._roots = roots
            self._snapshot = getattr(roots,"write_snapshot",None)
        except Exception as e:
            self._snapshot = None
            QMessageBox.critical(self, "Hiérarchie", str(e))
            return
        self._rebuild_tree()
        self._view_data.finish_load()

    def _rebuild_tree(self) -> None:
        selection = self._selected_node()
        if selection:
            self._selected_id = selection[0]
        def remember(item):
            self._expanded_states[item.data(0,_ROLE_ID)] = item.isExpanded()
            for i in range(item.childCount()):
                remember(item.child(i))
        if not getattr(self,"_last_query",""):
            for i in range(self._tree.topLevelItemCount()):
                remember(self._tree.topLevelItem(i))
        self._tree.clear()
        filt = normalize_search_text((self._search.text() or "").strip().casefold())
        self._last_query = filt

        def _matches(n: SubdivHierarchyNode) -> bool:
            if not filt:
                return True
            blob = normalize_search_text(f"{n.code} {n.libelle}".casefold())
            return filt in normalize_search_text(blob) or any(_matches(ch) for ch in n.children)

        def _add_node(parent_item: QTreeWidgetItem | None, node: SubdivHierarchyNode, parent_match=False) -> None:
            own_match = not filt or filt in normalize_search_text(f"{node.code} {node.libelle}")
            if not parent_match and not _matches(node):
                return
            pri = "" if node.pri_out is None else str(node.pri_out)
            label = f"{node.code} — {node.libelle}"
            if parent_item is None:
                item = QTreeWidgetItem([node.code, node.libelle, pri])
                self._tree.addTopLevelItem(item)
            else:
                item = QTreeWidgetItem([node.code, node.libelle, pri])
                parent_item.addChild(item)
            item.setData(0, _ROLE_ID, node.node_id)
            item.setData(0, _ROLE_LEVEL, node.level)
            if node.level == 1:
                item.setFont(0, QFont("", -1, QFont.Weight.DemiBold))
            if node.rsa_links:
                item.setToolTip(0, f"{node.rsa_links} affiliation(s) subdivision d'arme")
            for ch in node.children:
                _add_node(item, ch, parent_match or own_match)
            item.setExpanded(True if filt else self._expanded_states.get(node.node_id,True))
            if node.node_id == self._selected_id:
                self._tree.setCurrentItem(item)

        for root in self._roots:
            _add_node(None, root)
        self._update_detail()

    def _selected_node(self) -> tuple[int, int] | None:
        items = self._tree.selectedItems()
        if not items:
            return None
        it = items[0]
        nid = it.data(0, _ROLE_ID)
        lvl = it.data(0, _ROLE_LEVEL)
        if nid is None:
            return None
        return int(nid), int(lvl or 1)

    def _find_node(self, node_id: int) -> SubdivHierarchyNode | None:
        def _walk(nodes: list[SubdivHierarchyNode]) -> SubdivHierarchyNode | None:
            for n in nodes:
                if n.node_id == node_id:
                    return n
                found = _walk(n.children)
                if found:
                    return found
            return None

        return _walk(self._roots)

    def _check_editable(self) -> bool:
        from sme_gend.repositories.mysql_connection import mysql_database_key
        if self._snapshot is None or mysql_database_key(self._svc._settings)!=mysql_database_key(self._ref.settings):
            QMessageBox.warning(self,"Hierarchie","Connexion ou version indisponible. Fermez et rechargez cette fenetre avant de modifier.")
            return False
        st = evaluate_editing_state(self._ref.settings)
        if st.locked:
            QMessageBox.warning(self, "Édition verrouillée", st.detail or "")
            return False
        return True

    def _update_detail(self) -> None:
        sel = self._selected_node()
        if sel is None:
            self._detail_title.setText("Sélectionnez un nœud")
            self._detail_body.setText(
                "L'arbre comporte trois niveaux : <b>U</b> (Secun), <b>D</b> (Secdeux), "
                "<b>T</b> (Sectrois, feuille). Utilisez « Ajouter enfant » sous un U ou D."
            )
            return
        nid, lvl = sel
        node = self._find_node(nid)
        if node is None:
            return
        children, rsa = len(node.children), node.rsa_links
        next_lvl = {1: "D", 2: "T"}.get(lvl, "—")
        self._detail_title.setText(f"{node.level_label} — {node.code}")
        self._detail_body.setText(
            f"<b>{node.libelle}</b><br/>"
            f"Enfants : <b>{children}</b><br/>"
            f"Affiliations subdivision d'arme : <b>{rsa}</b><br/>"
            + (
                f"Prochain niveau possible : <b>{next_lvl}</b>"
                if lvl < 3
                else "Feuille — utilisée pour rattacher les SubArme par structure."
            )
        )

    def _on_add_u(self) -> None:
        if not self._check_editable():
            return
        expected = self._snapshot
        dlg = _NodeDialog(self, title="Nouveau niveau U (Secun)")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, lib, pri = dlg.values()
        if not code or not lib:
            QMessageBox.warning(self, "Hiérarchie", "Code et libellé obligatoires.")
            return
        try:
            run_reference_task(self,"Référentiel",lambda: self._svc.insert_u(code, lib, pri, expected=expected))
        except Exception as e:
            QMessageBox.critical(self, "Hiérarchie", str(e))
            return
        self.reload(force=True)

    def _on_add_child(self) -> None:
        if not self._check_editable():
            return
        expected = self._snapshot
        sel = self._selected_node()
        if sel is None:
            QMessageBox.information(self, "Hiérarchie", "Sélectionnez un nœud U ou D parent.")
            return
        nid, lvl = sel
        if lvl >= 3:
            QMessageBox.information(
                self,
                "Hiérarchie",
                "Un nœud T (feuille) ne peut pas avoir d'enfant.",
            )
            return
        child_label = "D (Secdeux)" if lvl == 1 else "T (Sectrois)"
        dlg = _NodeDialog(self, title=f"Nouveau niveau {child_label}")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, lib, pri = dlg.values()
        if not code or not lib:
            return
        try:
            run_reference_task(self,"Référentiel",lambda: self._svc.insert_child(nid, code, lib, pri, expected=expected))
        except Exception as e:
            QMessageBox.critical(self, "Hiérarchie", str(e))
            return
        self.reload(force=True)

    def _on_edit(self) -> None:
        if not self._check_editable():
            return
        expected = self._snapshot
        sel = self._selected_node()
        if sel is None:
            return
        nid, _ = sel
        node = self._find_node(nid)
        if node is None:
            return
        from sme_gend.repositories.mysql_connection import mysql_database_key
        draft_key=(mysql_database_key(self._svc._settings),nid)
        draft=self._node_drafts.get(draft_key,(node.code,node.libelle,node.pri_out))
        pri_s = "" if draft[2] is None else str(draft[2])
        dlg = _NodeDialog(
            self,
            title=f"Modifier {node.level_label}",
            code=draft[0],
            libelle=draft[1],
            pri_out=pri_s,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        code, lib, pri = dlg.values()
        if not lib:
            return
        if lib != node.libelle and not confirm_yes_no(
            self, "Portée du renommage",
            f"Renommer uniquement la nomenclature « {node.libelle} » en « {lib} » ?\n"
            f"{node.rsa_links} affiliation(s) connue(s). Les libellés des structures et les dossiers historiques restent inchangés.\n"
            "Pour modifier une subdivision d'une structure, utilisez Structures → Subdivisions.",
            default_no=True,
        ):
            return
        try:
            run_reference_task(self,"Référentiel",lambda: self._svc.update_node(nid, code, lib, pri, expected=expected))
        except Exception as e:
            self._node_drafts[draft_key]=(code,lib,pri)
            QMessageBox.critical(self, "Hiérarchie", str(e))
            return
        self._node_drafts.pop(draft_key,None)
        self.reload(force=True)

    def _on_propagate_rsa(self) -> None:
        if not self._check_editable():
            return
        expected = self._snapshot
        sel = self._selected_node()
        if sel is None:
            QMessageBox.information(
                self, "RSA", "Sélectionnez une feuille T (niveau 3) dans l'arbre."
            )
            return
        nid, lvl = sel
        if lvl != 3:
            QMessageBox.information(
                self,
                "RSA",
                "Seules les feuilles T (niveau 3) peuvent être propagées vers l'organigramme.",
            )
            return
        node = self._find_node(nid)
        if node is None:
            return
        try:
            propagation = run_reference_task(self,"Référentiel",lambda: self._svc.prepare_propagation(nid,expected=expected))
        except Exception as e:
            QMessageBox.critical(self,"RSA",str(e))
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Périmètre RSA")
        box.setText(
            f"Propager « {node.libelle} » vers quelles structures ?\n\n"
            "Recommandé : uniquement celles où la carrière porte déjà ce SubArme."
        )
        btn_carriere = box.addButton("Structures concernées", QMessageBox.ButtonRole.AcceptRole)
        btn_all = box.addButton("Toutes les structures", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked == box.button(QMessageBox.StandardButton.Cancel):
            return
        all_struct = clicked == btn_all
        try:
            n = run_reference_task(self,"Référentiel",lambda: self._svc.propagate_leaf_rsa(
                nid,
                libelle=node.libelle,
                all_structures=all_struct,
                expected=propagation,
            ))
        except Exception as e:
            QMessageBox.critical(self, "RSA", str(e))
            return
        QMessageBox.information(
            self,
            "RSA",
            f"{n} affiliation(s) subdivision d'arme créée(s) ou mises à jour.",
        )
        self.reload(force=True)

    def _on_delete(self) -> None:
        if not self._check_editable():
            return
        expected = self._snapshot
        sel = self._selected_node()
        if sel is None:
            return
        nid, _ = sel
        node = self._find_node(nid)
        if node is None:
            return
        children, rsa = len(node.children), node.rsa_links
        if not confirm_yes_no(
            self,
            "Supprimer",
            f"Supprimer « {node.code} — {node.libelle} » ?\n"
            f"Enfants : {children} · Affiliations : {rsa}",
            default_no=True,
        ):
            return
        try:
            run_reference_task(self,"Référentiel",lambda: self._svc.delete_node(nid, expected=expected))
        except Exception as e:
            QMessageBox.critical(self, "Suppression", str(e))
            return
        self.reload(force=True)
