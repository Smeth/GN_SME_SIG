"""Create missing nomenclature levels and attach their leaf in one operation."""
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QFormLayout,
    QComboBox, QLineEdit, QLabel, QGroupBox, QMessageBox)
from sme_gend.repositories.hierarchy_merge_guard import hierarchy_rows
from sme_gend.services.subdivision_creation import validate_creation_path


class SubdivisionCreateDialog(QDialog):
    def __init__(self, snapshot, parent=None, *, label=''):
        super().__init__(parent)
        self.setWindowTitle('Créer / rattacher une subdivision')
        self.resize(660, 480)
        self._snapshot = snapshot
        self._nodes, self._levels = hierarchy_rows(snapshot)
        self._levels_ui = []
        root = QVBoxLayout(self)
        hint = QLabel('Choisissez les niveaux existants ou créez ceux qui manquent. '
                      'La hiérarchie et son rattachement seront enregistrés ensemble.')
        hint.setWordWrap(True)
        root.addWidget(hint)
        for index, name in enumerate(('U', 'D', 'T')):
            group = QGroupBox(f'Niveau {name}')
            form = QFormLayout(group)
            combo, code, text = QComboBox(), QLineEdit(), QLineEdit()
            code.setMaxLength(64)
            text.setMaxLength(512)
            code.setPlaceholderText('Code du nouveau niveau')
            text.setPlaceholderText('Libellé du nouveau niveau')
            form.addRow('Niveau *', combo)
            form.addRow('Code *', code)
            form.addRow('Libellé *', text)
            self._levels_ui.append((combo, code, text, form))
            combo.currentIndexChanged.connect(lambda _i, level=index: self._changed(level))
            root.addWidget(group)
        self._label = QLineEdit(label)
        self._label.setMaxLength(512)
        self._label.setPlaceholderText('Vide : reprendre le libellé du niveau T')
        form = QFormLayout()
        form.addRow('Libellé dans la structure', self._label)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Créer / rattacher')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Annuler')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._populate(0)

    def _populate(self, index):
        combo, code, text, form = self._levels_ui[index]
        parent = 1 if index == 0 else self._levels_ui[index - 1][0].currentData()
        combo.blockSignals(True)
        combo.clear()
        choices = sorted((row for row in self._nodes.values()
                          if parent is not None and row[1] == parent and self._levels[row[0]] == index + 1),
                         key=lambda row: (str(row[2]), str(row[3])))
        if choices:
            combo.addItem('— Choisir —', -1)
        for row in choices:
            combo.addItem(f'{row[2]} — {row[3]}', row[0])
        combo.addItem('+ Créer ce niveau…', None)
        combo.blockSignals(False)
        self._changed(index)

    def _changed(self, index):
        combo, code, text, form = self._levels_ui[index]
        new = combo.currentData() is None
        form.setRowVisible(code, new)
        form.setRowVisible(text, new)
        if index < 2:
            self._populate(index + 1)

    def values(self):
        path = [combo.currentData() if combo.currentData() is not None
                else dict(code=code.text(), libelle=text.text())
                for combo, code, text, _ in self._levels_ui]
        path = validate_creation_path(self._snapshot, path)
        leaf = path[-1]
        label = self._label.text().strip() or (self._nodes[leaf][3] if isinstance(leaf, int) else leaf['libelle'])
        if not label:
            raise ValueError('Le libellé de la subdivision est obligatoire.')
        return path, str(label).strip()

    def accept(self):
        try:
            self.values()
        except ValueError as exc:
            QMessageBox.warning(self, 'Subdivision', str(exc))
            return
        super().accept()
