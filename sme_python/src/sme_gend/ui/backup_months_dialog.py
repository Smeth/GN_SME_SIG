"""Choose only months actually found in the selected backup."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout


class BackupMonthsDialog(QDialog):
    def __init__(self, months, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mois présents dans la sauvegarde')
        self.resize(440, 430)
        layout = QVBoxLayout(self)
        hint = QLabel('Cochez les mois à restaurer. Leurs données seront remplacées. '
                      'Les autres mois, les comptes et les paramètres du poste seront conservés. '
                      'Les passations de grade du dossier sont conservées et réappliquées selon leurs dates. '
                      'Une contradiction sur une même date de passation bloque la restauration.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._months = QListWidget()
        for month in sorted(set(months), reverse=True):
            item = QListWidgetItem(month, self._months)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(self._months, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText('Restaurer les mois cochés…')
        self._ok.setEnabled(False)
        self._months.itemChanged.connect(lambda _: self._ok.setEnabled(bool(self.selected_months())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_months(self):
        return tuple(sorted(self._months.item(i).text() for i in range(self._months.count())
                            if self._months.item(i).checkState() == Qt.CheckState.Checked))
