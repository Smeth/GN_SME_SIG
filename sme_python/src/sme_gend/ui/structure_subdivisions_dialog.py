"""Fenêtre modale : subdivisions d'arme (SubArme) liées à une structure dans ListeGenerale."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.ui.reference_conflict import show_reference_conflict

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QInputDialog,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.models.structure import StructureRow
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.services.structure_organigram_service import StructureOrganigramService
from sme_gend.ui.responsive_panels import WrapLayout


class StructureSubdivisionsDialog(QDialog):
    """Liste des SubArme distincts pour une structure + édition intégrée de la structure."""

    def __init__(
        self,
        parent,
        service: ReferentielService,
        structure: StructureRow,
        *,
        on_structure_changed: Callable[[], None] | None = None,
        allow_edit: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Subdivisions d'arme — {structure.code}")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setObjectName("StructureSubdivisionsDialog")

        self._service = service
        self._structure = structure
        self._on_structure_changed = on_structure_changed
        self._allow_edit = allow_edit
        self._org = StructureOrganigramService(service.settings)
        self._snapshot = None
        self._node = None

        title = QLabel(structure.designation or structure.code)
        title.setObjectName("modalTitle")
        title.setWordWrap(True)
        tf = QFont()
        tf.setPointSize(13)
        tf.setWeight(QFont.Weight.DemiBold)
        title.setFont(tf)

        sub = QLabel(
            f"Code structure : <b>{structure.code}</b><br/>"
            "Rattachements officiels et libellés observés dans les dossiers du mois consulté. "
            "La nomenclature U→D→T classe les unités ; les rattachements RSA les associent à cette structure. "
            "Une modification de nomenclature ne réécrit pas les dossiers historiques."
        )
        sub.setWordWrap(True)
        sub.setTextFormat(Qt.TextFormat.RichText)
        sub.setObjectName("modalSubtitle")

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.setMinimumHeight(200)

        self._btn_refresh = QPushButton("Actualiser la liste")
        self._btn_refresh.clicked.connect(lambda: self._reload_list(silent=False))

        btn_row = WrapLayout()
        btn_row.addWidget(self._btn_refresh)
        self._btn_add_sub = QPushButton("Créer / rattacher…")
        self._btn_rename_sub = QPushButton("Renommer…")
        self._btn_delete_sub = QPushButton("Supprimer le rattachement…")
        for button, action in ((self._btn_add_sub,'create'),(self._btn_rename_sub,'rename'),(self._btn_delete_sub,'delete')):
            button.clicked.connect(lambda checked=False,a=action:self._change_subdivision(a))
            button.setEnabled(allow_edit)
            btn_row.addWidget(button)
        self._list.currentItemChanged.connect(self._sync_subdivision_actions)

        # Édition intégrée (au lieu d'une modale imbriquée) : le code n'est pas
        # modifiable une fois la structure créée, seuls désignation et ordre le sont.
        self._g_edit = QGroupBox("Modifier la structure")
        self._g_edit.setObjectName("settingsPathsGroup")
        self._g_edit.setVisible(allow_edit)
        edit_form = QFormLayout()
        self._edit_designation = QLineEdit(structure.designation)
        self._edit_ordre = QLineEdit(
            "" if structure.ordre_tri is None else str(structure.ordre_tri)
        )
        self._edit_ordre.setPlaceholderText("Vide = aucun")
        edit_form.addRow("Désignation *", self._edit_designation)
        edit_form.addRow("Ordre tri", self._edit_ordre)
        self._btn_save_struct = QPushButton("Enregistrer les modifications")
        self._btn_save_struct.setObjectName("primaryButton")
        self._btn_save_struct.clicked.connect(self._save_structure_changes)
        self._btn_type = QPushButton("Type d'unité…")
        self._btn_type.clicked.connect(self._edit_type_unite)
        edit_lay = QVBoxLayout(self._g_edit)
        edit_lay.addLayout(edit_form)
        type_row = QHBoxLayout()
        type_row.addWidget(self._btn_save_struct)
        type_row.addWidget(self._btn_type)
        type_row.addStretch()
        edit_lay.addLayout(type_row)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        close_btn = bb.button(QDialogButtonBox.StandardButton.Close)
        if close_btn:
            close_btn.setText("Fermer")

        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addLayout(btn_row)
        lay.addWidget(self._list, stretch=1)
        lay.addWidget(self._g_edit)
        lay.addWidget(bb)

        self._reload_list(silent=True)

    def _reload_list(self, *, silent: bool = False) -> None:
        if not silent:
            if not confirm_yes_no(
                self,
                "Actualiser",
                "Recharger la liste des subdivisions depuis MySQL ?",
                default_no=False,
            ):
                return
        self._snapshot = None
        self._sync_subdivision_actions()
        try:
            snapshot = run_reference_task(self,"Référentiel",lambda: self._org.subdivision_editor_snapshot())
            tree = run_reference_task(self,"Référentiel",lambda: self._org.fetch_tree())
            node = next((n for n in tree if n.code==self._structure.code),None)
            if node is None:
                raise ValueError("Structure introuvable. Actualisez le module.")
        except Exception as e:
            self._list.clear()
            self._list.addItem("Lecture impossible. Actualisez avant toute modification.")
            QMessageBox.critical(self, "Subdivisions", str(e))
            return
        self._snapshot, self._node = snapshot, node
        self._list.clear()
        self._sync_subdivision_actions()
        if not node.subdivisions:
            self._list.addItem("(Aucune subdivision d'arme enregistrée pour cette structure)")
            i = self._list.item(0)
            if i:
                i.setFlags(Qt.ItemFlag.NoItemFlags)
            return
        for s in node.subdivisions:
            origin = "Rattachée au référentiel" if s.from_referentiel else "Donnée seule — à rattacher"
            self._list.addItem(f"{s.libelle} — {origin} · {s.agent_count} fiche(s) du mois")
            self._list.item(self._list.count()-1).setData(Qt.ItemDataRole.UserRole,s)
        self._sync_subdivision_actions()

    def _sync_subdivision_actions(self, *args):
        ready=self._allow_edit and self._snapshot is not None and self._org.settings==self._service.settings
        item=self._list.currentItem()
        selected=item.data(Qt.ItemDataRole.UserRole) if item else None
        self._btn_add_sub.setEnabled(ready)
        self._btn_rename_sub.setEnabled(ready and selected is not None and selected.rsa_id is not None)
        self._btn_delete_sub.setEnabled(ready and selected is not None and selected.rsa_id is not None)

    def _change_subdivision(self, action):
        from sme_gend.services.sme_editing_policy import evaluate_editing_state
        if not self._allow_edit or self._snapshot is None or self._org.settings!=self._service.settings:
            QMessageBox.warning(self,"Subdivisions","Rechargez les données avant cette action.")
            return
        if evaluate_editing_state(self._service.settings).locked:
            QMessageBox.warning(self,"Subdivisions","Édition verrouillée pour cette période.")
            return
        item=self._list.currentItem()
        selected=item.data(Qt.ItemDataRole.UserRole) if item else None
        if action!='create' and (selected is None or selected.rsa_id is None):
            QMessageBox.information(self,"Subdivisions","Sélectionnez un rattachement officiel. Pour une donnée seule, utilisez Créer / rattacher.")
            return
        label=selected.libelle if selected else ''
        leaf_id=None
        hierarchy_path=None
        if action=='create':
            from sme_gend.ui.subdivision_create_dialog import SubdivisionCreateDialog
            dialog = SubdivisionCreateDialog(self._snapshot, self, label=label)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            hierarchy_path, label = dialog.values()
        if action=='rename':
            label,ok=QInputDialog.getText(self,"Subdivision","Libellé dans cette structure :",text=label)
            if not ok or not label.strip():return
        scope=("Référentiel global et libellés des dossiers du mois consulté uniquement ; les autres périodes ne sont pas réécrites."
               if action=='rename' else "La suppression est refusée si une situation ou affectation utilise ce rattachement, même historique."
               if action=='delete' else "Création du rattachement officiel et association des dossiers correspondants du mois consulté.")
        if not confirm_yes_no(self,"Confirmer la portée",f"{self._structure.code} — {label}\n{scope}",default_no=True):return
        try:
            run_reference_task(self,"Référentiel",lambda: self._org.change_subdivision(action,self._node.structure_id,self._structure.code,label=label,
                rsa_id=selected.rsa_id if selected and action!='create' else None,leaf_id=leaf_id,expected=self._snapshot,
                hierarchy_path=hierarchy_path))
        except Exception as exc:
            QMessageBox.warning(self,"Subdivisions",str(exc))
            return
        self._reload_list(silent=True)
        if self._on_structure_changed:self._on_structure_changed()

    def _save_structure_changes(self) -> None:
        if not self._allow_edit:
            return
        designation = self._edit_designation.text().strip()
        if not designation:
            QMessageBox.warning(self, "Structure", "La désignation est obligatoire.")
            return
        ordre_txt = self._edit_ordre.text().strip()
        ordre: int | None = None
        if ordre_txt:
            try:
                ordre = int(ordre_txt)
            except ValueError:
                QMessageBox.warning(self, "Structure", "Ordre de tri : nombre entier attendu.")
                return
        code = self._structure.code
        if not confirm_yes_no(
            self,
            "Confirmer",
            f"Enregistrer les modifications de la structure « {code} » dans ref_structure (MySQL) ?",
            default_no=True,
        ):
            return
        try:
            run_reference_task(self,"Référentiel",lambda: self._service.structure_update(
                code,
                designation,
                ordre,
                famille_unite=self._structure.famille_unite,
                expected=self._structure,
            ))
        except ReferenceConflictError as exc:
            show_reference_conflict(self, exc)
            return
        except Exception as e:
            QMessageBox.critical(self, "Enregistrement", str(e))
            return
        fresh = run_reference_task(self,"Référentiel",lambda: self._service.structures())
        self._structure = next((s for s in fresh if s.code == code), self._structure)
        self._edit_designation.setText(self._structure.designation)
        self._edit_ordre.setText(
            "" if self._structure.ordre_tri is None else str(self._structure.ordre_tri)
        )
        if self._on_structure_changed is not None:
            self._on_structure_changed()

    def _edit_type_unite(self) -> None:
        if not self._allow_edit:
            return
        from sme_gend.ui.structure_edit_dialog import StructureEditDialog

        dlg = StructureEditDialog(self, self._structure)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            code, designation, ordre, famille = dlg.values()
            if not designation:
                QMessageBox.warning(self, "Structure", "La désignation est obligatoire.")
                return
            if not confirm_yes_no(
                self,
                "Confirmer",
                f"Enregistrer le type d'unité pour « {code} » ?",
                default_no=False,
            ):
                return
            try:
                run_reference_task(self,"Référentiel",lambda: self._service.structure_update(
                    code, designation, ordre, famille_unite=famille, expected=self._structure
                ))
            except ReferenceConflictError as exc:
                show_reference_conflict(self, exc, draft=True)
                continue
            except Exception as e:
                QMessageBox.critical(self, "Enregistrement", str(e))
                return
            self._structure = next((row for row in run_reference_task(self,"Référentiel",lambda: self._service.structures()) if row.code==code), self._structure)
            self._edit_designation.setText(self._structure.designation)
            self._edit_ordre.setText("" if self._structure.ordre_tri is None else str(self._structure.ordre_tri))
            if self._on_structure_changed is not None:
                self._on_structure_changed()
            return
