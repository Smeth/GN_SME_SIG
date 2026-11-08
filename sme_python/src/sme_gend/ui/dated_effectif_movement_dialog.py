"""A dedicated register for real dates, independent from the legacy month grid."""
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class DatedMovementEditDialog(QDialog):
    def __init__(self, settings, parent=None, *, existing=None):
        super().__init__(parent)
        self._settings, self._existing = settings, existing
        self._agent_id = existing.agent_id if existing else None
        self.setWindowTitle('Corriger un mouvement daté' if existing else 'Nouveau mouvement daté')
        self.setMinimumWidth(540)
        self._agent = QLineEdit(existing.agent_name if existing else '')
        self._agent.setReadOnly(True)
        self._agent.setPlaceholderText('Choisir dans le personnel, y compris hors effectif')
        pick = QPushButton('Rechercher…')
        pick.setEnabled(existing is None)
        pick.clicked.connect(self._pick_agent)
        agent_row = QHBoxLayout()
        agent_row.addWidget(self._agent, 1)
        agent_row.addWidget(pick)
        self._category = QComboBox()
        self._category.addItems(['GAIN', 'PERTE'])
        self._kind = QLineEdit(existing.movement_kind if existing else '')
        self._kind.setMaxLength(64)
        self._kind.setPlaceholderText('Ex. recrutement, réintégration, retraite, décès…')
        self._date = FrenchDateEdit()
        self._date.set_from_text((existing.effective_date if existing else date.today()).isoformat())
        self._reference = QLineEdit(existing.reference_note or '' if existing else '')
        self._reference.setMaxLength(256)
        self._reference.setPlaceholderText('Facultatif')
        self._details = QPlainTextEdit(existing.details or '' if existing else '')
        self._details.setMaximumHeight(80)
        self._details.setPlaceholderText('Facultatif')
        self._reason = QLineEdit()
        self._reason.setMaxLength(2000)
        self._reason.setPlaceholderText('Obligatoire pour une correction ou une date hors du mois de travail')
        if existing:
            self._category.setCurrentText(existing.category)
        form = QFormLayout()
        form.addRow('Agent *', agent_row)
        form.addRow('Mouvement *', self._category)
        form.addRow('Nature / motif *', self._kind)
        form.addRow('Date réelle *', self._date)
        form.addRow('Réf/note', self._reference)
        form.addRow('Précisions', self._details)
        form.addRow('Motif de correction *' if existing else 'Motif de saisie historique', self._reason)
        info = QLabel('Un seul mouvement net par agent et par jour. Une date historique peut être déclarée '
                      'avec autorisation et motif, même sans archive de cette année.')
        info.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Enregistrer')
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(info)
        layout.addWidget(buttons)

    def _pick_agent(self):
        from sme_gend.ui.agent_pick_dialog import AgentPickDialog
        from sme_gend.services.dated_effectif_movement import resolve_selected_agent
        dialog = AgentPickDialog(self._settings, self, active_effectif_only=False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            agent_id = resolve_selected_agent(self._settings, dialog.selected_legacy_id())
        except Exception as exc:
            QMessageBox.warning(self, 'Agent', str(exc))
            return
        self._agent_id = agent_id
        self._agent.setText(dialog.selected_nom() or str(agent_id))

    def _validate(self):
        stamp = self._date.to_datetime()
        if self._agent_id is None or stamp is None or not self._kind.text().strip():
            QMessageBox.warning(self, 'Mouvement', 'L’agent, la nature et la date réelle sont obligatoires.')
            return
        if stamp.date() > date.today():
            QMessageBox.warning(self, 'Mouvement', 'La date réelle ne peut pas être future.')
            return
        if self._existing and not self._reason.text().strip():
            QMessageBox.warning(self, 'Correction', 'Le motif de correction est obligatoire.')
            return
        if len(self._details.toPlainText()) > 10000:
            QMessageBox.warning(self, 'Mouvement', 'Les précisions sont limitées à 10 000 caractères.')
            return
        self.accept()

    def values(self):
        return dict(category=self._category.currentText(), movement_kind=self._kind.text().strip(),
                    effective_date=self._date.to_datetime().date(), reference_note=self._reference.text().strip() or None,
                    details=self._details.toPlainText().strip() or None, reason=self._reason.text().strip())


class DatedEffectifMovementDialog(QDialog):
    def __init__(self, settings, parent=None, *, month=None):
        super().__init__(parent)
        if not session_has_permission('situation.gain_perte'):
            raise PermissionError('Consultation des mouvements datés non autorisée.')
        self._settings, self._records, self._fresh, self._generation = settings, [], False, 0
        self._offset, self._size, self._more = 0, 200, False
        self.setWindowTitle('Mouvements datés de l’effectif')
        self.resize(1180, 740)
        self.setMinimumSize(820, 480)
        self._month = QLineEdit(month or '')
        self._month.setPlaceholderText('Mois AAAA-MM · vide : tous')
        self._month.setMaximumWidth(230)
        self._month.returnPressed.connect(self._filter_changed)
        self._previous, self._next = QPushButton('Précédente'), QPushButton('Suivante')
        self._previous.clicked.connect(lambda: self._page(-1))
        self._next.clicked.connect(lambda: self._page(1))
        refresh = QPushButton('Actualiser')
        refresh.clicked.connect(self.reload)
        self._shortcut = QShortcut(QKeySequence('F5'), self)
        self._shortcut.activated.connect(self.reload)
        filters = QHBoxLayout()
        filters.addWidget(self._month)
        filters.addWidget(refresh)
        filters.addStretch()
        filters.addWidget(self._previous)
        filters.addWidget(self._next)
        self._message = QLabel('Dates réelles déclarées ; les observations historiques Access restent conservées séparément.')
        self._message.setWordWrap(True)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(['Date réelle', 'Agent', 'Mouvement', 'Nature / motif', 'Réf/note', 'Saisi le', 'Auteur'])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._actions)
        self._detail = QLabel()
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setWordWrap(True)
        self._detail.setMaximumHeight(55)
        self._new, self._edit, self._delete = QPushButton('Ajouter…'), QPushButton('Corriger…'), QPushButton('Supprimer…')
        self._new.clicked.connect(self._create)
        self._edit.clicked.connect(self._correct)
        self._delete.clicked.connect(self._remove)
        close = QPushButton('Fermer')
        close.clicked.connect(self.reject)
        actions = QHBoxLayout()
        for button in (self._new, self._edit, self._delete):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addLayout(filters)
        layout.addWidget(self._message)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._detail)
        layout.addLayout(actions)
        self._actions()
        self.reload()

    def _selected(self):
        row = self._table.currentRow()
        return self._records[row] if 0 <= row < len(self._records) else None

    def _actions(self):
        view = self._fresh and session_has_permission('situation.gain_perte')
        write = view and session_has_permission('effectifs.edition')
        correction = write and session_has_admin_role() and session_has_permission('workflow.correct_movement')
        selected = self._selected()
        self._new.setEnabled(bool(write))
        self._edit.setEnabled(bool(correction and selected))
        self._delete.setEnabled(bool(correction and selected))
        self._previous.setEnabled(bool(view and self._offset))
        self._next.setEnabled(bool(view and self._more))
        self._detail.setText(selected.details or '' if view and selected else '')

    def _filter_changed(self):
        self._offset = 0
        self.reload()

    def _page(self, delta):
        if self._fresh and (delta < 0 and self._offset or delta > 0 and self._more):
            self._offset = max(0, self._offset + delta * self._size)
            self.reload()

    def reload(self, *_):
        self._generation += 1
        generation, offset, month = self._generation, self._offset, self._month.text().strip() or None
        self._fresh = False
        self._actions()
        self._message.setText('Chargement des mouvements datés…')
        def work():
            from sme_gend.services.dated_effectif_movement import list_movements
            return list_movements(self._settings, month=month, limit=self._size + 1, offset=offset)
        def loaded(rows):
            if generation != self._generation:
                return
            if not session_has_permission('situation.gain_perte'):
                rows = []
                self._message.setText('Consultation non autorisée.')
            else:
                self._fresh = True
                self._message.setText(f'Page {offset // self._size + 1} · {min(len(rows), self._size)} mouvement(s) daté(s).')
            self._more = len(rows) > self._size
            self._records = list(rows[:self._size])
            self._table.setRowCount(len(self._records))
            for index, row in enumerate(self._records):
                values = (row.effective_date.strftime('%d/%m/%Y'), row.agent_name, row.category, row.movement_kind,
                          row.reference_note, row.recorded_at.strftime('%d/%m/%Y %H:%M'), row.recorded_by)
                for col, value in enumerate(values):
                    self._table.setItem(index, col, QTableWidgetItem(str(value or '—')))
            self._actions()
        def failed(error):
            if generation == self._generation:
                self._records = []
                self._table.setRowCount(0)
                self._message.setText('Registre indisponible : ' + str(error))
                self._actions()
        run_in_background(self, work, loaded, on_error=failed)

    def _mutate(self, work):
        from sme_gend.repositories.mysql_connection import CommittedMirrorError
        def guarded():
            try:
                work()
                return ''
            except CommittedMirrorError as exc:
                return 'Enregistrement confirmé dans MySQL ; copie locale du journal à vérifier. ' + str(exc)
        result = run_with_progress(self, 'Mouvement daté', 'Enregistrement et recalcul des situations concernées…', guarded)
        if result is not None:
            if result:
                QMessageBox.warning(self, 'Mouvement enregistré', result)
            self.reload()

    def _create(self):
        if not self._fresh or not self._new.isEnabled():
            return
        dialog = DatedMovementEditDialog(self._settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            from sme_gend.services.dated_effectif_movement import create_movement
            self._mutate(lambda: create_movement(self._settings, dialog._agent_id, **dialog.values()))

    def _correct(self):
        selected = self._selected()
        if not self._fresh or selected is None or not self._edit.isEnabled():
            return
        dialog = DatedMovementEditDialog(self._settings, self, existing=selected)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            from sme_gend.services.dated_effectif_movement import update_movement
            self._mutate(lambda: update_movement(self._settings, selected, **dialog.values()))

    def _remove(self):
        selected = self._selected()
        if not self._fresh or selected is None or not self._delete.isEnabled():
            return
        reason, accepted = QInputDialog.getMultiLineText(self, 'Supprimer le mouvement daté',
            f'{selected.category} · {selected.agent_name} · {selected.effective_date:%d/%m/%Y}\nMotif de suppression obligatoire :')
        if not accepted:
            return
        if not reason.strip():
            QMessageBox.warning(self, 'Suppression', 'Le motif de suppression est obligatoire.')
            return
        from sme_gend.services.dated_effectif_movement import delete_movement
        self._mutate(lambda: delete_movement(self._settings, selected, reason=reason))


def open_dated_effectif_movements(parent, settings, *, month=None):
    try:
        dialog = DatedEffectifMovementDialog(settings, parent, month=month)
    except PermissionError as exc:
        QMessageBox.warning(parent, 'Mouvements datés', str(exc))
        return
    dialog.exec()
