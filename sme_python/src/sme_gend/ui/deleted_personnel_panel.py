"""Section Paramètres — personnel supprimé (audit) et restauration."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.auth_guard import (
    session_can_restore_personnel,
    session_has_permission,
)
from sme_gend.services.personnel_deleted_service import (
    DeletedPersonnelEntry,
    DeletedPersonnelStatus,
    format_deleted_status,
    list_deleted_personnel,
)
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.sme_editing_policy import evaluate_editing_state_for_settings
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class _DeletedPersonnelModel:
    """Stockage simple pour le tableau (évite un gros QAbstractTableModel dédié)."""

    HEADERS = (
        "Date (UTC)",
        "Nom",
        "Motif sortie",
        "Mat. mil.",
        "Mat. solde",
        "Id carrière",
        "Supprimé par",
        "Mois SME",
        "Statut",
    )

    def __init__(self) -> None:
        self._entries: list[DeletedPersonnelEntry] = []
        self._display: list[tuple[str, ...]] = []

    def set_entries(self, entries: list[DeletedPersonnelEntry]) -> None:
        self._entries = entries
        self._display = []
        for e in entries:
            row = e.row
            self._display.append(
                (
                    (e.ts_utc or "")[:19].replace("T", " "),
                    e.display_name,
                    e.loss_motif or "—",
                    (row.matricule_militaire if row else "") or "",
                    (row.matricule_solde if row else "") or "",
                    e.deleted_career_id or "",
                    e.edited_by or "",
                    e.sme_mois_context or "",
                    self._status_label(e),
                )
            )

    @staticmethod
    def _status_label(e: DeletedPersonnelEntry) -> str:
        base = format_deleted_status(e)
        detail = (e.status_detail or "").strip()
        if detail and e.status != DeletedPersonnelStatus.RESTORABLE:
            return f"{base} — {detail}"
        return base

    def entry_at(self, row: int) -> DeletedPersonnelEntry | None:
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def row_count(self) -> int:
        return len(self._display)

    def cell(self, row: int, col: int) -> str:
        return self._display[row][col]


class DeletedPersonnelPanel(QGroupBox):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__("Fiches retirées de la liste (restauration)", parent)
        self._settings = settings
        self._service = PersonnelService(settings)
        self._model = _DeletedPersonnelModel()
        self._entries: list[DeletedPersonnelEntry] = []

        hint = QLabel(
            "Chaque suppression exige un <b>motif de sortie</b> (décès, retraite, etc.) "
            "et crée une ligne <b>Perte</b> dans Gains & pertes. "
            "La restauration réactive le segment sur le <b>mois SME courant</b> "
            "(même identifiant carrière lorsque possible). "
            "En cas de <b>conflit de matricule</b>, vous pouvez saisir d'autres "
            "valeurs ou laisser le champ vide. "
            "Droit de restauration : « Restaurer des fiches retirées de la liste »."
        )
        hint.setWordWrap(True)
        hint.setObjectName("dashSubtitle")

        self._table = QTableView()
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(220)

        self._btn_refresh = QPushButton("Actualiser")
        self._btn_refresh.clicked.connect(self.reload)

        self._btn_restore = QPushButton("Restaurer la sélection")
        self._btn_restore.setObjectName("primaryButton")
        self._btn_restore.clicked.connect(self._on_restore)

        btn_row = QHBoxLayout()
        btn_row.addWidget(self._btn_refresh)
        btn_row.addWidget(self._btn_restore)
        btn_row.addStretch()

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(self._table)
        lay.addLayout(btn_row)

        self._sync_buttons()
        self.reload()

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._service.rebind_settings(settings)
        self.reload()

    def _sync_buttons(self) -> None:
        can_restore = session_can_restore_personnel()
        self._btn_restore.setEnabled(can_restore)
        self._btn_restore.setVisible(
            session_has_permission("system.settings") or can_restore
        )

    def reload(self) -> None:
        from sme_gend.services.auth_guard import session_can_delete_personnel

        if (
            not session_has_permission("system.settings")
            and not session_can_restore_personnel()
            and not session_can_delete_personnel()
        ):
            self.setVisible(False)
            return
        self.setVisible(True)
        self._sync_buttons()
        try:
            entries = list_deleted_personnel(self._settings)
        except Exception as exc:
            QMessageBox.warning(self, "Fiches retirées de la liste", str(exc))
            entries = []
        self._entries = entries
        self._model.set_entries(entries)
        self._rebuild_table_view()

    def _rebuild_table_view(self) -> None:
        from sme_gend.ui.simple_table_model import SimpleRowTableModel

        tm = SimpleRowTableModel(list(_DeletedPersonnelModel.HEADERS), self._table)
        tm.set_rows(self._model._display)
        self._table.setModel(tm)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table_model = tm

    def _selected_entry(self) -> DeletedPersonnelEntry | None:
        idxs = self._table.selectionModel().selectedRows()
        if not idxs:
            return None
        row = idxs[0].row()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _on_restore(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(
            self,
            "effectifs.restore",
            message="Votre compte n'a pas la permission de restaurer des fiches supprimées.",
        ):
            return
        st = evaluate_editing_state_for_settings(self._settings)
        if st.locked:
            QMessageBox.warning(self, "Restauration impossible", st.detail)
            return
        entry = self._selected_entry()
        if entry is None:
            QMessageBox.information(self, "Restauration", "Sélectionnez une ligne.")
            return
        if entry.row is None:
            QMessageBox.warning(self, "Restauration impossible", "Snapshot d'audit incomplet.")
            return
        if entry.status == DeletedPersonnelStatus.ALREADY_PRESENT:
            detail = entry.status_detail or format_deleted_status(entry)
            QMessageBox.information(self, "Restauration", detail)
            return
        if entry.status == DeletedPersonnelStatus.NO_SNAPSHOT:
            QMessageBox.warning(self, "Restauration impossible", entry.status_detail or "Snapshot manquant.")
            return
        row_to_restore = entry.row
        if entry.status == DeletedPersonnelStatus.MATRICULE_CONFLICT:
            from sme_gend.ui.restore_personnel_matricule_dialog import pick_restore_matricules

            adjusted = pick_restore_matricules(
                self,
                self._settings,
                entry.row,
                entry.deleted_career_id,
                conflict_detail=entry.status_detail,
            )
            if adjusted is None:
                return
            row_to_restore = adjusted
        name = entry.display_name
        if not confirm_yes_no(
            self,
            "Restaurer la fiche",
            f"Restaurer « {name} » (carrière id {entry.deleted_career_id}) "
            f"dans le mois SME actif ?\n\n"
            "Le segment sera réactivé sur ce mois (si la sortie avait été une erreur).",
            default_no=True,
        ):
            return
        try:
            new_id = self._service.restore_deleted_personnel(
                row_to_restore,
                deleted_career_id=entry.deleted_career_id,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Restauration", str(exc))
            return
        QMessageBox.information(
            self,
            "Restauration",
            f"Fiche restaurée : {name}\nNouvel id carrière : {new_id}",
        )
        self.reload()
