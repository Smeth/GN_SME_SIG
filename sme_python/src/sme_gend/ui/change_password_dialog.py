"""Dialogue — changement de mot de passe par l'utilisateur connecté."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.auth_service import AuthService, MIN_PASSWORD_LENGTH


class ChangeOwnPasswordDialog(QDialog):
    """Saisie de l'ancien mot de passe + nouveau (self-service)."""

    def __init__(
        self,
        auth: AuthService,
        username: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._auth = auth
        self._username = username
        self.setWindowTitle("Changer mon mot de passe")
        self.setModal(True)
        self.setMinimumWidth(420)

        intro = QLabel(
            f"Compte : <b>{username}</b><br>"
            "Le nouveau mot de passe doit contenir au moins "
            f"{MIN_PASSWORD_LENGTH} caractères."
        )
        intro.setWordWrap(True)

        self._current = QLineEdit()
        self._current.setEchoMode(QLineEdit.EchoMode.Password)
        self._current.setPlaceholderText("Mot de passe actuel")
        self._new = QLineEdit()
        self._new.setEchoMode(QLineEdit.EchoMode.Password)
        self._new.setPlaceholderText(f"Nouveau ({MIN_PASSWORD_LENGTH} car. min.)")
        self._confirm = QLineEdit()
        self._confirm.setEchoMode(QLineEdit.EchoMode.Password)
        self._confirm.setPlaceholderText("Confirmer le nouveau mot de passe")

        self._show_pwd = QCheckBox("Afficher les mots de passe")
        self._show_pwd.toggled.connect(self._toggle_visibility)

        self._err = QLabel()
        self._err.setObjectName("loginError")
        self._err.setWordWrap(True)
        self._err.hide()

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Actuel :", self._current)
        form.addRow("Nouveau :", self._new)
        form.addRow("Confirmation :", self._confirm)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Enregistrer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self._show_pwd)
        lay.addWidget(self._err)
        lay.addWidget(buttons)

        self._current.setFocus()

    def _toggle_visibility(self, checked: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal
            if checked
            else QLineEdit.EchoMode.Password
        )
        for field in (self._current, self._new, self._confirm):
            field.setEchoMode(mode)

    def _submit(self) -> None:
        current = self._current.text()
        new_pwd = self._new.text()
        confirm = self._confirm.text()
        if not current:
            self._show_error("Indiquez votre mot de passe actuel.")
            return
        if len(new_pwd) < MIN_PASSWORD_LENGTH:
            self._show_error(
                f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
            )
            return
        if new_pwd != confirm:
            self._show_error("Les deux nouveaux mots de passe ne correspondent pas.")
            return
        try:
            self._auth.change_own_password(current, new_pwd)
        except ValueError as exc:
            self._show_error(str(exc))
            return
        self.accept()

    def _show_error(self, message: str) -> None:
        self._err.setText(message)
        self._err.show()
