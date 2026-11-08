"""One shared publication register, reached from numerical reports and monthly workflow."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from sme_gend.services.auth_guard import require_permission, session_has_permission, session_has_admin_role
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file

STATUS_LABELS = {"brouillon": "Brouillon", "valide": "Validée", "remplace": "Remplacée"}


def _text(value):
    return value.strftime("%d/%m/%Y %H:%M") if hasattr(value, "strftime") else str(value or "—")


class PublicationRegistryDialog(QDialog):
    def __init__(self, settings, parent=None, *, month=None):
        super().__init__(parent)
        if not session_has_permission("publications.view"):
            raise PermissionError("Consultation des versions SME non autorisée.")
        self._settings, self._generation, self._fresh = settings, 0, False
        self._offset, self._page_size, self._has_more = 0, 100, False
        self._records = []
        self.setWindowTitle("Versions et validations SME")
        self.resize(1180, 740)
        self.setMinimumSize(760, 460)
        self._month = QLineEdit(month or "")
        self._month.setPlaceholderText("Mois YYYY-MM · vide : tous")
        self._month.setMaximumWidth(240)
        self._status = QComboBox()
        self._status.addItem("Tous les états", None)
        for code, label in STATUS_LABELS.items():
            self._status.addItem(label, code)
        self._refresh = QPushButton("Actualiser")
        self._refresh.clicked.connect(self.reload)
        self._reload_shortcut = QShortcut(QKeySequence("F5"), self)
        self._reload_shortcut.activated.connect(self.reload)
        self._month.returnPressed.connect(self._filter_changed)
        self._status.currentIndexChanged.connect(self._filter_changed)
        self._previous = QPushButton("Précédente")
        self._next = QPushButton("Suivante")
        self._previous.clicked.connect(lambda: self._change_page(-1))
        self._next.clicked.connect(lambda: self._change_page(1))
        filters = QHBoxLayout()
        filters.addWidget(self._month)
        filters.addWidget(self._status)
        filters.addStretch()
        filters.addWidget(self._previous)
        filters.addWidget(self._next)
        filters.addWidget(self._refresh)
        self._message = QLabel("Chargement du registre partagé…")
        self._message.setWordWrap(True)
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels(["Version", "Mois", "État", "Créée le", "Auteur",
                                               "Validée le", "Validateur", "Remplacée par"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._update_actions)
        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setMaximumHeight(80)
        self._verify = QPushButton("Vérifier")
        self._validate = QPushButton("Valider…")
        self._replace = QPushButton("Remplacer la validée…")
        self._download = QPushButton("Télécharger le classeur…")
        self._verify.clicked.connect(self._on_verify)
        self._validate.clicked.connect(self._on_validate)
        self._replace.clicked.connect(self._on_replace)
        self._download.clicked.connect(self._on_download)
        actions = QHBoxLayout()
        for button in (self._verify, self._validate, self._replace, self._download):
            actions.addWidget(button)
        actions.addStretch()
        close = QPushButton("Fermer")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self._message)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._detail)
        layout.addLayout(actions)
        self._update_actions()
        self.reload()

    def _selected(self):
        row = self._table.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _update_actions(self):
        record = self._selected()
        ready = self._fresh and record is not None and session_has_permission("publications.view")
        self._previous.setEnabled(self._fresh and self._offset > 0)
        self._next.setEnabled(self._fresh and self._has_more)
        for button, permission in ((self._verify, "verify"), (self._download, "download"),
                                    (self._validate, "validate"), (self._replace, "replace")):
            enabled = ready and session_has_permission("publications." + permission)
            if permission == "download":
                enabled = enabled and session_has_permission("effectifs.export")
            if permission in {"validate", "replace"}:
                enabled = enabled and record.status == "brouillon" and session_has_admin_role()
            button.setEnabled(bool(enabled))
        if record is None:
            self._detail.clear()
        else:
            note = getattr(record, "validation_note", "") or ""
            self._detail.setText(f"Référence : {record.revision_id}" + (f" · {note}" if note else ""))

    def reload(self, *_):
        if not require_permission(self, "publications.view"):
            self._fresh = False
            self._update_actions()
            return
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        raw = self._month.text().strip()
        month = canonical_sme_mois_yyyy_mm(raw) if raw else None
        if raw and not month:
            QMessageBox.warning(self, "Mois invalide", "Indiquez un mois valide au format YYYY-MM.")
            return
        self._generation += 1
        generation, settings, status, offset = self._generation, self._settings, self._status.currentData(), self._offset
        selected = self._selected()
        selected_id = selected.id if selected else None
        self._fresh = False
        self._refresh.setEnabled(False)
        self._message.setText("Chargement du registre partagé…")
        self._update_actions()
        def work():
            from sme_gend.services.sme_publication_registry import list_publications
            return list_publications(settings, mois=month, status=status, limit=self._page_size + 1, offset=offset)
        def loaded(records):
            if generation != self._generation:
                return
            self._refresh.setEnabled(True)
            if not session_has_permission("publications.view"):
                self._records = []
                self._table.setRowCount(0)
                self._message.setText("Consultation non autorisée.")
                self._update_actions()
                return
            self._has_more = len(records) > self._page_size
            self._records = list(records[:self._page_size])
            self._table.setRowCount(len(self._records))
            for index, record in enumerate(self._records):
                values = (f"{record.revision_no}", record.mois, STATUS_LABELS.get(record.status, record.status),
                    _text(record.created_at), record.created_by, _text(record.validated_at),
                    record.validated_by, _text(record.replaced_by_id))
                for col, value in enumerate(values):
                    self._table.setItem(index, col, QTableWidgetItem(str(value or "—")))
                if record.id == selected_id:
                    self._table.selectRow(index)
            self._fresh = True
            self._message.setText(f"Page {offset // self._page_size + 1} · {len(self._records)} version(s) · les versions remplacées restent conservées.")
            self._update_actions()
        def failed(error):
            if generation == self._generation:
                self._refresh.setEnabled(True)
                self._message.setText(f"Registre indisponible : {error}")
                self._fresh = False
                self._update_actions()
        run_in_background(self, work, loaded, on_error=failed)

    def _filter_changed(self, *_):
        self._offset = 0
        self.reload()

    def _change_page(self, direction):
        if not self._fresh or (direction > 0 and not self._has_more):
            return
        self._offset = max(0, self._offset + direction * self._page_size)
        self.reload()

    def _allowed_selection(self, permission):
        if not require_permission(self, "publications.view") or not require_permission(self, permission):
            return None
        return self._selected() if self._fresh else None

    def _on_verify(self):
        record = self._allowed_selection("publications.verify")
        if record is None:
            return
        from sme_gend.services.sme_publication_registry import verify_publication
        result = run_with_progress(self, "Vérification SME", "Contrôle des fichiers conservés…",
                                   lambda: verify_publication(self._settings, record.id))
        if result is not None:
            QMessageBox.information(self, "Version vérifiée", f"Version {record.revision_no} · {record.mois} : fichiers intègres.")

    def _note(self, *, replacement=False, context=""):
        dialog = QDialog(self)
        dialog.setWindowTitle("Remplacer la version validée" if replacement else "Valider cette version")
        dialog.setMinimumWidth(480)
        note = QPlainTextEdit()
        note.setMaximumHeight(150)
        layout = QVBoxLayout(dialog)
        if context:
            summary = QLabel(context)
            summary.setWordWrap(True)
            layout.addWidget(summary)
        layout.addWidget(QLabel("Motif du remplacement *" if replacement else "Note de validation (facultative)"))
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        def accept():
            if replacement and not note.toPlainText().strip():
                QMessageBox.warning(dialog, "Motif obligatoire", "Indiquez le motif du remplacement.")
                return
            dialog.accept()
        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return note.toPlainText().strip() if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _on_validate(self):
        record = self._allowed_selection("publications.validate")
        if record is None or record.status != "brouillon":
            return
        note = self._note(context=f"Mois {record.mois} · valider la version {record.revision_no} comme document de référence de ce mois.")
        if note is None:
            return
        from sme_gend.services.sme_publication_registry import validate_publication
        result = run_with_progress(self, "Validation SME", "Vérification et validation de la version…",
            lambda: validate_publication(self._settings, record.id, expected_version=record.version, note=note))
        if result is not None:
            if getattr(result, "operation_warning", ""):
                QMessageBox.warning(self, "Validation enregistrée", result.operation_warning)
            self.reload()

    def _on_replace(self):
        record = self._allowed_selection("publications.replace")
        if record is None or record.status != "brouillon":
            return
        from sme_gend.services.sme_publication_registry import list_publications, replace_publication
        current = run_with_progress(self, "Version validée", "Recherche de la validation actuelle…",
            lambda: list_publications(self._settings, mois=record.mois, status="valide", limit=2))
        if current is None:
            return
        if len(current) != 1:
            QMessageBox.warning(self, "Remplacement", "Une seule version validée doit être présente. Actualisez le registre.")
            return
        reason = self._note(replacement=True, context=f"Mois {record.mois} · remplacer la version validée {current[0].revision_no} "
                            f"par la version {record.revision_no}. La version précédente restera conservée.")
        if reason is None:
            return
        result = run_with_progress(self, "Remplacement SME", "Enregistrement de la nouvelle version validée…",
            lambda: replace_publication(self._settings, record.id, expected_version=record.version,
                                       expected_current_id=current[0].id, reason=reason))
        if result is not None:
            if getattr(result, "operation_warning", ""):
                QMessageBox.warning(self, "Remplacement enregistré", result.operation_warning)
            self.reload()

    def _on_download(self):
        record = self._allowed_selection("publications.download")
        if record is None or not require_permission(self, "effectifs.export"):
            return
        filename = f"SME_{record.mois}_v{record.revision_no}.xlsx"
        destination, _ = QFileDialog.getSaveFileName(self, "Télécharger la version SME", str(Path.home() / filename), "Excel (*.xlsx)")
        if not destination or not confirm_overwrite_file(self, destination):
            return
        from sme_gend.services.sme_publication_registry import download_publication
        result = run_with_progress(self, "Téléchargement SME", "Vérification et copie du classeur conservé…",
            lambda: download_publication(self._settings, record.id, Path(destination)))
        if result is not None:
            if getattr(result, "operation_warning", ""):
                QMessageBox.warning(self, "Classeur téléchargé", str(destination) + "\n\n" + result.operation_warning)
            else:
                QMessageBox.information(self, "Classeur téléchargé", str(destination))


def open_publication_registry(parent, settings, *, month=None):
    if not require_permission(parent, "publications.view"):
        return
    PublicationRegistryDialog(settings, parent, month=month).exec()
