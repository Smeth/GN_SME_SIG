"""Modifier une fonction et choisir explicitement ses rattachements."""

from __future__ import annotations

from sme_gend.services.shared_reference_write import shared_reference_write
from sme_gend.ui.reference_task import run_reference_task
from sme_gend.services.search_text import normalize_search_text

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_editing_policy import evaluate_editing_state


def open_fonction_smart_dialog(
    parent: QWidget | None,
    service: ReferentielService,
    *,
    libelle: str,
    structure_code: str | None = None,
    subdivision: str | None = None,
    structure_designation: str | None = None,
    structure_famille: str | None = None,
    fonction_id: int | None = None,
) -> str | None:
    """Ouvre le dialogue ; retourne le libellé enregistré ou None."""
    st = evaluate_editing_state(service.settings)
    if st.locked:
        QMessageBox.warning(parent, "Édition verrouillée", st.detail)
        return None
    dlg = FonctionSmartDialog(
        service,
        parent,
        libelle=libelle,
        structure_code=structure_code,
        subdivision=subdivision,
        structure_designation=structure_designation,
        structure_famille=structure_famille,
        fonction_id=fonction_id,
    )
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.saved_libelle()


class FonctionSmartDialog(QDialog):
    """Renomme / crée une fonction et remplace ses rattachements explicites."""

    def __init__(
        self,
        service: ReferentielService,
        parent: QWidget | None,
        *,
        libelle: str,
        structure_code: str | None = None,
        subdivision: str | None = None,
        structure_designation: str | None = None,
        structure_famille: str | None = None,
        fonction_id: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._fonction_id = fonction_id
        self._links_load_error=False
        self._settings = service.settings
        from sme_gend.ui.bg_task import run_with_progress
        def load():
            try:
                return service.fonctions_all(), service.fonction_links_snapshot_all()
            except Exception:
                return None
        loaded = run_with_progress(self,"Fonction","Lecture du catalogue et des rattachements…",load,silent=True)
        if loaded is None or service.settings != self._settings:
            self._catalog_snapshot=[];self._links_snapshot=None;self._links_load_error=True
        else:
            self._catalog_snapshot,self._links_snapshot=loaded
        self._structure_code = (structure_code or "").strip() or None
        self._subdivision = (subdivision or "").strip() or None
        self._structure_designation = structure_designation
        self._structure_famille = structure_famille
        initial = (libelle or "").strip()
        self._saved_libelle = ""
        self._is_new = fonction_id is None and not (libelle or "").strip()
        self.setWindowTitle(
            "Nouvelle fonction" if self._is_new else "Fonction — modifier et rattacher"
        )
        self.setMinimumSize(520, 420)

        intro = QLabel(
            "Cochez les structures autorisées. Seuls les rattachements déjà enregistrés sont cochés. "
            "Décocher retire le rattachement ; aucune case cochée conserve une fonction sans structure. "
            "La structure du filtre est seulement repérée, sans sélection automatique."
        )
        if self._links_load_error:
            intro.setText("Impossible de charger les fonctions et rattachements. Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
        intro.setWordWrap(True)
        intro.setObjectName("gainPerteSummary")

        self._libelle = QLineEdit(initial)
        self._libelle.setPlaceholderText("Ex. CHEF DE SECTION, DIRECTEUR DPF…")

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._structure_search=QLineEdit()
        self._structure_search.setPlaceholderText("Filtrer les structures (sans modifier les coches)…")
        self._structure_search.setClearButtonEnabled(True)
        self._structure_search.textChanged.connect(self._filter_structures)

        row_btns = QHBoxLayout()
        btn_all = QPushButton("Tout cocher")
        btn_none = QPushButton("Tout décocher")
        from sme_gend.ui.check_all import bind_list_check_all
        bind_list_check_all(btn_all, self._list, self._structure_search)
        btn_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        row_btns.addWidget(btn_all)
        row_btns.addWidget(btn_none)
        row_btns.addStretch()

        form = QFormLayout()
        form.addRow("Libellé *", self._libelle)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setText(
            "Créer" if self._is_new else "Enregistrer"
        )
        bb.button(QDialogButtonBox.StandardButton.Save).setEnabled(not self._links_load_error)
        bb.accepted.connect(self._on_save)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(QLabel("Structures autorisées"))
        lay.addWidget(self._structure_search)
        lay.addWidget(self._list, stretch=1)
        lay.addLayout(row_btns)
        lay.addWidget(bb)

        self._load_manual_structures()

    def saved_libelle(self) -> str:
        return self._saved_libelle

    def _set_all(self, state: Qt.CheckState) -> None:
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is not None and not self._list.isRowHidden(i):
                item.setCheckState(state)

    def _filter_structures(self,text):
        needle=normalize_search_text(text)
        for i in range(self._list.count()):
            item=self._list.item(i)
            self._list.setRowHidden(i,bool(needle) and needle not in normalize_search_text(item.text()))

    def _load_manual_structures(self):
        if self._links_load_error:return
        from sme_gend.services.structure_label_format import structure_combo_label
        from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees
        fid=self._fonction_id
        if fid is None:
            row=next((r for r in self._catalog_snapshot if r.libelle.casefold()==self._libelle.text().strip().casefold()),None)
            fid=row.id if row else None
        choices=getattr(self._links_snapshot,'choices',None)
        if choices is None:
            from sme_gend.repositories.structure_repository import StructureRepository
            choices=[(None,r.code,r.designation) for r in StructureRepository(self._settings).fetch_all()]
        linked_ids={r[0] for r in getattr(self._links_snapshot,'links',()) if r[1]==fid}
        for row in choices:
            sid,code,designation=row[:3]
            if structure_fonctions_non_liees(code):continue
            item=QListWidgetItem(structure_combo_label(code,designation))
            item.setData(Qt.ItemDataRole.UserRole,code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if sid in linked_ids else Qt.CheckState.Unchecked)
            if code==self._structure_code:item.setToolTip("Structure du filtre courant — sélection manuelle.")
            self._list.addItem(item)

    def _selected_codes(self) -> list[str]:
        codes: list[str] = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            code = item.data(Qt.ItemDataRole.UserRole)
            if code:
                codes.append(str(code))
        return codes

    def _resolve_fonction_id(self, libelle: str) -> int:
        if self._fonction_id is not None:
            row=next((row for row in self._catalog_snapshot if row.id==self._fonction_id),None)
            if row is None:
                raise ValueError("Fonction indisponible. Actualisez les données avant de recommencer.")
            if libelle != row.libelle:
                self._service.fonction_rename_confirmed(row,libelle,self._rename_impact)
            else:
                self._service.fonction_update(row.id,row.libelle,libelle,expected=row)
            return row.id
        for row in self._catalog_snapshot:
            if row.libelle.casefold()==libelle.casefold():
                self._service.fonction_update(row.id,row.libelle,row.libelle,expected=row)
                return row.id
        return self._service.fonction_insert(libelle)

    def _on_save(self) -> None:
        if getattr(self,"_links_load_error",False):
            QMessageBox.warning(self,"Fonction indisponible","Fermez puis rouvrez cette fenêtre après vérification de la connexion.")
            return
        lib = self._libelle.text().strip()
        if not lib:
            QMessageBox.warning(self, "Fonction", "Indiquez un libellé.")
            return
        codes = self._selected_codes()
        if self._service.settings != self._settings:
            QMessageBox.warning(self,"Contexte modifié","Fermez puis rouvrez cette fenêtre dans le contexte courant.")
            return
        try:
            row=next((r for r in self._catalog_snapshot if r.id==self._fonction_id),None)
            if row is not None and lib!=row.libelle:
                from sme_gend.ui.fonction_rename_confirmation import confirm_global_rename
                self._rename_impact=confirm_global_rename(self,self._service,row,lib)
                if self._rename_impact is None:return
            def save():
                with shared_reference_write(self._service.settings):
                    self._service.validate_links_snapshot(self._links_snapshot)
                    fid = self._resolve_fonction_id(lib)
                    current = self._service.fonction_links_snapshot(fid)
                    self._service.fonction_set_structure_links(fid, codes, expected=current)
            run_reference_task(self,"Enregistrement de la fonction",save)
        except ValueError as e:
            QMessageBox.warning(self, "Fonction", str(e))
            return
        except Exception as e:
            QMessageBox.critical(self, "Fonction", str(e))
            return
        self._saved_libelle = lib
        self.accept()
