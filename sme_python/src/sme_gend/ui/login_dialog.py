"""Fenêtre de connexion (authentification activée)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.config.auth_config import DEFAULT_ADMIN_PASSWORD
from sme_gend.config.branding import APP_NAME, APP_SUBTITLE
from sme_gend.services.auth_service import MIN_PASSWORD_LENGTH, AuthService
from sme_gend.ui.resources import app_icon_path, load_app_icon


class LoginDialog(QDialog):
    """UI de connexion moderne (carte centrée, fond sombre)."""

    def __init__(
        self,
        auth: AuthService,
        parent=None,
        *,
        lock_mode: bool = False,
        username_hint: str = "",
    ) -> None:
        super().__init__(parent)
        self._auth = auth
        self._session = None
        self._drag_pos: QPoint | None = None
        self._lock_mode = lock_mode
        self._quit_requested = False
        self.setWindowTitle(
            "Session verrouillée — reconnexion"
            if lock_mode
            else f"Connexion — {APP_NAME}"
        )
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowIcon(load_app_icon())
        self.setObjectName("loginDialog")
        # Pas de barre de titre Windows (réduire/agrandir/fermer) : chrome maison
        # ci-dessous (en-tête + bouton fermer), plus cohérent avec la carte stylée.
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("loginHeaderBar")
        header.setFixedHeight(36)
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 0, 8, 0)
        header_lay.setSpacing(8)
        header_title = QLabel(APP_NAME)
        header_title.setObjectName("loginHeaderTitle")
        btn_close = QPushButton("×")
        btn_close.setObjectName("loginCloseBtn")
        btn_close.setToolTip("Fermer")
        btn_close.setFixedSize(26, 26)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setAutoDefault(False)
        btn_close.setDefault(False)
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.clicked.connect(self.reject)
        header_lay.addWidget(header_title)
        header_lay.addStretch()
        if not lock_mode:
            header_lay.addWidget(btn_close)
        outer.addWidget(header)
        self._header_bar = header

        # Contenu centré (carte + accroche) avec ses propres marges symétriques —
        # distinctes de l'en-tête, qui doit lui rester collé aux bords de la fenêtre.
        body = QVBoxLayout()
        body.setContentsMargins(32, 24, 32, 32)
        body.setSpacing(0)
        outer.addLayout(body, 1)

        card = QFrame()
        card.setObjectName("loginCard")
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(28, 28, 28, 28)
        card_lay.setSpacing(14)

        title = QLabel(APP_NAME)
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(APP_SUBTITLE)
        sub.setObjectName("loginSubtitle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_lay.addWidget(title)
        card_lay.addWidget(sub)
        card_lay.addSpacing(8)

        self._user = QLineEdit()
        self._user.setObjectName("loginField")
        self._user.setPlaceholderText("Identifiant")
        self._user.setClearButtonEnabled(True)
        last = auth.config.last_username if auth.config.remember_last_username else ""
        if username_hint:
            self._user.setText(username_hint)
            self._user.setReadOnly(True)
        elif last:
            self._user.setText(last)

        self._pass = QLineEdit()
        self._pass.setObjectName("loginField")
        self._pass.setPlaceholderText("Mot de passe")
        self._pass.setEchoMode(QLineEdit.EchoMode.Password)

        self._show_pwd = QCheckBox("Afficher le mot de passe")
        self._show_pwd.toggled.connect(self._on_toggle_password)

        self._lbl_err = QLabel()
        self._lbl_err.setObjectName("loginError")
        self._lbl_err.setWordWrap(True)
        self._lbl_err.hide()

        self._btn_login = QPushButton("Se connecter")
        self._btn_login.setObjectName("loginPrimaryBtn")
        self._btn_login.setAutoDefault(True)
        self._btn_login.setDefault(True)
        self._btn_login.clicked.connect(self._try_login)

        self._pass.returnPressed.connect(self._try_login)
        self._user.returnPressed.connect(self._pass.setFocus)

        card_lay.addWidget(QLabel("Identifiant"))
        card_lay.addWidget(self._user)
        card_lay.addWidget(QLabel("Mot de passe"))
        card_lay.addWidget(self._pass)
        card_lay.addWidget(self._show_pwd)
        card_lay.addWidget(self._lbl_err)
        card_lay.addSpacing(6)
        card_lay.addWidget(self._btn_login)

        body.addStretch()
        body.addWidget(card)
        body.addStretch()

        hint = QLabel(
            "Session verrouillée — reconnectez-vous pour continuer"
            if lock_mode
            else "Authentification requise · session limitée dans le temps"
        )
        hint.setObjectName("loginHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addSpacing(16)
        body.addWidget(hint)

        if lock_mode:
            btn_quit = QPushButton("Quitter l'application")
            btn_quit.setObjectName("loginSecondaryBtn")
            btn_quit.setAutoDefault(False)
            btn_quit.setDefault(False)
            btn_quit.clicked.connect(self._request_quit)
            body.addSpacing(10)
            body.addWidget(btn_quit, alignment=Qt.AlignmentFlag.AlignCenter)

        logo = app_icon_path()
        if logo is not None:
            from PySide6.QtGui import QIcon

            pix = QIcon(str(logo)).pixmap(48, 48)
            badge = QLabel()
            badge.setPixmap(pix)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_lay.insertWidget(0, badge)

    def _header_contains(self, pos: QPoint) -> bool:
        return self._header_bar.geometry().contains(pos)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and self._header_contains(pos):
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            # Repli si startSystemMove() n'est pas disponible (ancienne plateforme/Qt) :
            # suivi manuel du delta souris.
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _on_toggle_password(self, checked: bool) -> None:
        self._pass.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )

    def _request_quit(self) -> None:
        self._quit_requested = True
        self.reject()

    def quit_requested(self) -> bool:
        return self._quit_requested

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            focus = self.focusWidget()
            if focus is self._user:
                self._pass.setFocus()
            else:
                self._try_login()
            event.accept()
            return
        if self._lock_mode and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)

    def _try_login(self) -> None:
        self._lbl_err.hide()
        self._btn_login.setEnabled(False)
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
        QApplication.processEvents()
        try:
            session = self._auth.authenticate(self._user.text(), self._pass.text())
        except ValueError as exc:
            self._auth.last_login_error = str(exc)
            session = None
        finally:
            QApplication.restoreOverrideCursor()
            self._btn_login.setEnabled(True)

        if session is None:
            self._lbl_err.setText(
                self._auth.last_login_error or "Identifiant ou mot de passe incorrect."
            )
            self._lbl_err.show()
            self._pass.clear()
            self._pass.setFocus()
            return
        if session.must_change_password:
            if not self._force_password_change(session.username):
                # L'utilisateur a annulé le changement obligatoire : pas de connexion
                # avec le mot de passe par défaut encore actif.
                self._auth.logout()
                self._pass.clear()
                self._pass.setFocus()
                return
        self._session = session
        self.accept()

    def _force_password_change(self, username: str) -> bool:
        """
        Changement de mot de passe obligatoire (compte encore sur le mot de passe
        par défaut). Retourne True si un nouveau mot de passe a été défini.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle("Mot de passe par défaut — changement requis")
        dlg.setModal(True)
        dlg.setMinimumWidth(380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(
            "Ce compte utilise encore le mot de passe par défaut.<br>"
            "Définissez un nouveau mot de passe pour continuer."
        ))

        pwd1 = QLineEdit()
        pwd1.setPlaceholderText(f"Nouveau mot de passe ({MIN_PASSWORD_LENGTH} car. min.)")
        pwd1.setEchoMode(QLineEdit.EchoMode.Password)
        pwd2 = QLineEdit()
        pwd2.setPlaceholderText("Confirmer le nouveau mot de passe")
        pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        err = QLabel()
        err.setObjectName("loginError")
        err.setWordWrap(True)
        err.hide()

        lay.addWidget(pwd1)
        lay.addWidget(pwd2)
        lay.addWidget(err)

        btn_ok = QPushButton("Valider")
        btn_ok.setDefault(True)
        lay.addWidget(btn_ok)

        result: list[bool] = []

        def _validate() -> None:
            p1, p2 = pwd1.text(), pwd2.text()
            if len(p1) < MIN_PASSWORD_LENGTH:
                err.setText(
                    f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
                )
                err.show()
                return
            if p1 != p2:
                err.setText("Les deux mots de passe ne correspondent pas.")
                err.show()
                return
            if p1 == DEFAULT_ADMIN_PASSWORD:
                err.setText("Choisissez un mot de passe différent du mot de passe par défaut.")
                err.show()
                return
            try:
                self._auth.change_own_password(DEFAULT_ADMIN_PASSWORD, p1)
            except ValueError as e:
                err.setText(str(e))
                err.show()
                return
            result.append(True)
            dlg.accept()

        btn_ok.clicked.connect(_validate)
        pwd2.returnPressed.connect(_validate)

        dlg.exec()
        return bool(result)

    def session(self):
        return self._session

    @staticmethod
    def run_login(auth: AuthService, parent=None):
        if not auth.is_enabled():
            return True, None
        if not auth.has_users():
            QMessageBox.warning(
                parent,
                "Authentification",
                "L'authentification est activée mais aucun utilisateur n'est défini.\n\n"
                "Désactivez l'authentification dans Utilisateurs & droits "
                "ou créez un compte administrateur.",
            )
            return False, None
        dlg = LoginDialog(auth, parent)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False, None
        return True, dlg.session()

    @staticmethod
    def run_relogin(
        auth: AuthService,
        parent=None,
        *,
        username_hint: str = "",
    ):
        """Reconnexion après expiration — pas de fermeture sans mot de passe valide."""
        if not auth.is_enabled():
            return True, None
        dlg = LoginDialog(
            auth,
            parent,
            lock_mode=True,
            username_hint=username_hint,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False, None, dlg.quit_requested()
        return True, dlg.session(), False
