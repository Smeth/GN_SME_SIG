"""Reset choices and credentials for replacing all existing users."""
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QCheckBox, QLabel,
    QFormLayout, QLineEdit, QDialogButtonBox, QMessageBox)
from sme_gend.services.application_reset import ResetOptions


class ApplicationResetDialog(QDialog):
    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Réinitialisation complète")
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        label = QLabel("Base concernée : " + target + "\n\n"
            "Supprime tous les agents, mois, grades, diplômes, stages, tâches, référentiels "
            "et réglages partagés. Les archives mensuelles et médias du poste sont retirés. "
            "Les sauvegardes et documents déjà exportés restent disponibles.\n\n"
            "Une sauvegarde complète sera créée avant toute suppression. "
            "Tous les postes utilisant cette base sont concernés. Fermez les autres sessions.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.keep_users = QCheckBox("Conserver les utilisateurs, rôles et mots de passe")
        self.keep_documents = QCheckBox("Conserver l'historique des documents générés")
        self.keep_users.setChecked(True)
        self.keep_documents.setChecked(True)
        layout.addWidget(self.keep_users)
        layout.addWidget(self.keep_documents)
        from PySide6.QtWidgets import QWidget
        self.credentials = QWidget(self)
        form = QFormLayout(self.credentials)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.repeat = QLineEdit()
        self.repeat.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Nouveau super administrateur", self.username)
        form.addRow("Nouveau mot de passe", self.password)
        form.addRow("Confirmer le mot de passe", self.repeat)
        layout.addWidget(self.credentials)
        self.credentials.setVisible(False)
        self.keep_users.toggled.connect(lambda keep: self.credentials.setVisible(not keep))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Continuer...")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self.keep_users.isChecked():
            from sme_gend.services.auth_service import validate_password
            try:
                if not self.username.text().strip() or len(self.username.text().strip()) > 64:
                    raise ValueError("Renseignez le nouvel identifiant (64 caractères maximum).")
                validate_password(self.password.text())
                if self.password.text() != self.repeat.text():
                    raise ValueError("Les mots de passe ne correspondent pas.")
            except ValueError as exc:
                QMessageBox.warning(self, "Nouveau compte", str(exc))
                return
        self.accept()

    def options(self):
        return ResetOptions(self.keep_users.isChecked(), self.keep_documents.isChecked())
