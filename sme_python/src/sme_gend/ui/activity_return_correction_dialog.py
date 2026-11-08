"""A compact administrative return correction, separate from ordinary editing."""
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QVBoxLayout)
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class ActivityReturnCorrectionDialog(QDialog):
    def __init__(self, parent, snapshot):
        super().__init__(parent)
        self.setWindowTitle('Corriger un retour validé')
        self.setMinimumWidth(480)
        self._date = FrenchDateEdit()
        self._date.set_from_text(snapshot.return_date.isoformat())
        self._reference = QLineEdit(snapshot.reference or '')
        self._reference.setMaxLength(256)
        self._reference.setPlaceholderText('Facultatif')
        self._reason = QPlainTextEdit()
        self._reason.setMaximumHeight(90)
        self._reason.setPlaceholderText('Expliquez la correction')
        intro = QLabel(f'{snapshot.agent_name} — {snapshot.position_label}\n'
                       f'Début : {snapshot.start_date:%d/%m/%Y} · Retour validé : {snapshot.return_date:%d/%m/%Y}')
        intro.setWordWrap(True)
        consequence = QLabel('Les situations concernées seront recalculées. Les documents déjà validés '
                             'restent conservés ; leur correction exige une nouvelle version. '
                             'Cette correction historique est tracée et ne rouvre pas les mois clôturés.')
        consequence.setWordWrap(True)
        form = QFormLayout()
        form.addRow('Date réelle de fin / retour *', self._date)
        form.addRow('Réf/note', self._reference)
        form.addRow('Motif *', self._reason)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Enregistrer la correction')
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(consequence)
        layout.addWidget(buttons)

    def _validate(self):
        if self._date.to_datetime() is None or not self._reason.toPlainText().strip():
            QMessageBox.warning(self, 'Correction', 'La date réelle et le motif sont obligatoires.')
            return
        self.accept()

    def values(self):
        return self._date.to_datetime().date(), self._reason.toPlainText().strip(), self._reference.text().strip() or None


def run_activity_return_correction(parent, settings, agent_id):
    from sme_gend.services.activity_return_correction import list_activity_returns, correct_activity_return
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    try:
        snapshots = list_activity_returns(settings, agent_id)
        if not snapshots:
            QMessageBox.information(parent, 'Correction', 'Aucun retour validé rattaché à cet agent.')
            return False
        selected = snapshots[0]
        if len(snapshots) > 1:
            labels = [f'{item.position_label} · début {item.start_date:%d/%m/%Y} · retour {item.return_date:%d/%m/%Y} · fiche {item.source_id}'
                      for item in snapshots]
            value, ok = QInputDialog.getItem(parent, 'Retour à corriger', 'Choisissez le retour validé :', labels, 0, False)
            if not ok:
                return False
            selected = snapshots[labels.index(value)]
        dialog = ActivityReturnCorrectionDialog(parent, selected)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        actual, reason, reference = dialog.values()
        correct_activity_return(settings, selected, actual, reason, reference=reference)
    except CommittedMirrorError as exc:
        QMessageBox.warning(parent, 'Correction enregistrée', str(exc))
        return True
    except Exception as exc:
        QMessageBox.warning(parent, 'Correction non enregistrée', str(exc))
        return False
    QMessageBox.information(parent, 'Correction', 'Correction enregistrée et situations concernées recalculées.')
    return True
