"""Utilisateurs, rôles et authentification — administration des comptes et droits."""

from __future__ import annotations

from sme_gend.services.search_text import normalize_search_text

from dataclasses import dataclass, replace
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, QDate, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from sme_gend.ui.no_wheel_widgets import NoWheelComboBox, NoWheelSpinBox

from sme_gend.config.auth_config import (
    CONCURRENT_SESSIONS_LIMITED,
    CONCURRENT_SESSIONS_OFF,
    CONCURRENT_SESSIONS_SINGLE,
    DEFAULT_LOCKOUT_MINUTES,
    DEFAULT_MAX_CONCURRENT_SESSIONS,
    DEFAULT_MAX_FAILED_ATTEMPTS,
    DEFAULT_SESSION_TIMEOUT_MINUTES,
    MAX_CONCURRENT_SESSIONS,
    MAX_LOCKOUT_MINUTES,
    MAX_MAX_FAILED_ATTEMPTS,
    MAX_SESSION_TIMEOUT_MINUTES,
    MIN_CONCURRENT_SESSIONS,
    MIN_LOCKOUT_MINUTES,
    MIN_MAX_FAILED_ATTEMPTS,
    MIN_SESSION_TIMEOUT_MINUTES,
    AuthConfig,
)
from sme_gend.config.auth_permissions import (
    ADMIN_ONLY_PERMISSIONS,
    ROLE_ADMIN,
    ROLE_OPERATEUR,
    is_super_admin_role,
    is_system_protected_role,
    role_permissions_locked,
    role_may_grant_permission,
)
from sme_gend.services.auth_context import get_app_auth
from sme_gend.config.ui_labels import display_ui_title, MONTH_MANAGEMENT_TITLE, ADMIN_TOOLS_TITLE, REMOVED_RECORDS_TITLE
from sme_gend.services.auth_guard import (
    session_can_delete_user_accounts,
    session_can_manage_auth_settings,
    session_can_manage_user_accounts,
    session_can_manage_user_roles,
    session_can_view_auth_journal,
    session_can_view_user_accounts,
    session_is_super_admin,
    session_has_admin_role,
)
from sme_gend.services.auth_service import MIN_PASSWORD_LENGTH, AuthService
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.view_lifecycle import ViewDataState
from sme_gend.ui.responsive_panels import scroll_panel


@dataclass(frozen=True, slots=True)
class _AuthReloadPayload:
    cfg: AuthConfig
    audit_lines: list[dict[str, Any]]
    uses_default_password: bool = False


def _lock_state_label(user) -> str:
    if getattr(user, "disabled", False):
        return "Désactivé"
    raw = (getattr(user, "locked_until", "") or "").strip()
    if not raw:
        return "OK"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return "OK"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if dt <= now:
        return "OK"
    remaining = max(1, int((dt - now).total_seconds() // 60) + 1)
    if (dt - now).total_seconds() > 86400 * 365:
        return "Verrouillé (administrateur)"
    return f"Verrouillé ({remaining} min)"


def _user_is_locked(user) -> bool:
    if getattr(user, "disabled", False):
        return False
    return _lock_state_label(user).startswith("Verrouillé")


def _format_ts(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "—"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    return local.strftime("%d/%m/%Y %H:%M")


# Actions du journal auth (valeur stockée → libellé FR)
AUTH_JOURNAL_ACTION_LABELS: tuple[tuple[str, str], ...] = (
    ("login", "Connexion réussie"),
    ("logout", "Déconnexion"),
    ("login_failed", "Échec de connexion"),
    ("login_blocked", "Connexion bloquée"),
    ("user_created", "Compte créé"),
    ("user_updated", "Compte modifié"),
    ("user_renamed", "Compte renommé"),
    ("user_deleted", "Compte supprimé"),
    ("user_locked", "Compte verrouillé"),
    ("user_unlocked", "Compte déverrouillé"),
    ("password_changed", "Mot de passe changé"),
    ("password_changed_self", "Mot de passe changé (soi-même)"),
    ("password_reset", "Mot de passe réinitialisé"),
    ("role_created", "Rôle créé"),
    ("role_updated", "Rôle modifié"),
    ("role_deleted", "Rôle supprimé"),
    ("auth_settings", "Paramètres de connexion"),
    ("session_revoked", "Session clôturée"),
    ("sessions_revoked_all", "Toutes sessions clôturées"),
)


def _auth_action_label(action: str) -> str:
    key = (action or "").strip()
    for code, label in AUTH_JOURNAL_ACTION_LABELS:
        if code == key:
            return label
    return key or "—"


class _NewRoleDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau rôle")
        self.setModal(True)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        hint = QLabel("Les rôles système ne peuvent pas être supprimés. Les permissions d'Opérateur et Lecteur sont personnalisables ; celles des administrateurs sont fixes.")
        hint.setWordWrap(True)
        hint.setWordWrap(True)
        hint.setObjectName("settingsPathsHint")
        lay.addWidget(hint)
        form = QFormLayout()
        form.setSpacing(10)
        self._role_id = QLineEdit()
        self._role_id.setPlaceholderText("ex. chef_section")
        self._role_name = QLineEdit()
        self._role_name.setPlaceholderText("Nom affiché")
        self._role_desc = QLineEdit()
        self._role_desc.setPlaceholderText("Description (optionnel)")
        form.addRow("Identifiant :", self._role_id)
        form.addRow("Nom :", self._role_name)
        form.addRow("Description :", self._role_desc)
        lay.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Créer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def values(self) -> tuple[str, str, str]:
        return (
            self._role_id.text().strip(),
            self._role_name.text().strip(),
            self._role_desc.text().strip(),
        )


class _PasswordDialog(QDialog):
    def __init__(self, username: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau mot de passe")
        self.setModal(True)
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(QLabel(f"Nouveau mot de passe pour « {username} » :"))
        self._pwd1 = QLineEdit()
        self._pwd1.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd1.setPlaceholderText(f"{MIN_PASSWORD_LENGTH} caractères minimum")
        self._pwd2 = QLineEdit()
        self._pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd2.setPlaceholderText("Confirmer le mot de passe")
        self._err = QLabel()
        self._err.setObjectName("loginError")
        self._err.setWordWrap(True)
        self._err.hide()
        lay.addWidget(self._pwd1)
        lay.addWidget(self._pwd2)
        lay.addWidget(self._err)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Valider")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        p1, p2 = self._pwd1.text(), self._pwd2.text()
        if len(p1) < MIN_PASSWORD_LENGTH:
            self._err.setText(
                f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères."
            )
            self._err.show()
            return
        if p1 != p2:
            self._err.setText("Les deux mots de passe ne correspondent pas.")
            self._err.show()
            return
        self.accept()

    def password(self) -> str:
        return self._pwd1.text()


class _RenameUserDialog(QDialog):
    def __init__(self, current_username: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Renommer l'identifiant")
        self.setModal(True)
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.addWidget(
            QLabel(
                f"Nouvel identifiant pour « {current_username} » "
                "(lettres, chiffres, . _ - ; commence par une lettre) :"
            )
        )
        self._ident = QLineEdit()
        self._ident.setPlaceholderText("ex. dupont.m")
        self._err = QLabel()
        self._err.setObjectName("loginError")
        self._err.setWordWrap(True)
        self._err.hide()
        lay.addWidget(self._ident)
        lay.addWidget(self._err)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Renommer")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _validate(self) -> None:
        if len(self._ident.text().strip()) < 2:
            self._err.setText("Identifiant trop court.")
            self._err.show()
            return
        self.accept()

    def new_username(self) -> str:
        return self._ident.text().strip()


class _AccountDetailScroll(QScrollArea):
    """Le contenu garde sa hauteur : la barre atteint le bas du formulaire."""

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        body = self.widget()
        lay = body.layout() if body is not None else None
        if body is None or lay is None:
            return
        hint_h = lay.sizeHint().height()
        if hint_h > 0 and body.minimumHeight() != hint_h:
            body.setMinimumHeight(hint_h)


class UsersAuthView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auth = get_app_auth() or AuthService()
        self._role_perm_checks: dict[str, QCheckBox] = {}
        self._selected_role_id: str | None = None
        self._reload_busy = False
        self._log_loaded = False
        self._log_lines: list[dict[str, Any]] = []
        self._reload_worker = None
        self._view_data = ViewDataState(stale=True)
        self._user_baseline = None
        self._role_baseline = None
        self._auth_baseline = None
        self._detail_username = None
        self._uses_default_password = False
        self._write_busy = False
        self._sessions_busy = False


        self._status_lbl = QLabel("Chargement…")
        self._status_lbl.setObjectName("settingsPathsHint")

        self._warn_banner = QLabel()
        self._warn_banner.setObjectName("usersAuthWarnBanner")
        self._warn_banner.setWordWrap(True)
        self._warn_banner.setTextFormat(Qt.TextFormat.RichText)
        self._warn_banner.hide()

        tab_users = self._build_users_tab()
        tab_roles = self._build_roles_tab()
        tab_auth = self._build_auth_tab()
        tab_sessions = self._build_sessions_tab()
        tab_log = self._build_log_tab()

        self._tabs = QTabWidget()
        self._tabs.setObjectName("settingsTabs")
        self._tabs.addTab(scroll_panel(tab_users), "Comptes")
        self._tabs.addTab(tab_roles, "Rôles")
        self._tabs.addTab(tab_auth, "Connexion")
        self._tabs.addTab(tab_sessions, "Sessions actives")
        self._tabs.addTab(scroll_panel(tab_log), "Journal")
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._log_date_from.setDate(QDate(2000, 1, 1))
        self._log_date_to.setDate(QDate(2000, 1, 1))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(5)
        root.addWidget(self._status_lbl)
        root.addWidget(self._warn_banner)
        root.addWidget(self._tabs, stretch=1)
        self._set_loading_ui(True)

    def _build_auth_tab(self) -> QWidget:
        self._chk_enabled = QCheckBox("Exiger une connexion au lancement de l'application")
        self._chk_remember_user = QCheckBox("Mémoriser le dernier identifiant sur l'écran de connexion")
        self._spin_timeout = NoWheelSpinBox()
        self._spin_timeout.setRange(MIN_SESSION_TIMEOUT_MINUTES, MAX_SESSION_TIMEOUT_MINUTES)
        self._spin_timeout.setSingleStep(15)
        self._spin_timeout.setSuffix(" min")
        self._spin_timeout.setValue(DEFAULT_SESSION_TIMEOUT_MINUTES)
        self._spin_timeout.setMinimumWidth(160)
        self._spin_lock_attempts = NoWheelSpinBox()
        self._spin_lock_attempts.setRange(MIN_MAX_FAILED_ATTEMPTS, MAX_MAX_FAILED_ATTEMPTS)
        self._spin_lock_attempts.setValue(DEFAULT_MAX_FAILED_ATTEMPTS)
        self._spin_lock_attempts.setMinimumWidth(160)
        self._spin_lock_minutes = NoWheelSpinBox()
        self._spin_lock_minutes.setRange(MIN_LOCKOUT_MINUTES, MAX_LOCKOUT_MINUTES)
        self._spin_lock_minutes.setSuffix(" min")
        self._spin_lock_minutes.setValue(DEFAULT_LOCKOUT_MINUTES)
        self._spin_lock_minutes.setMinimumWidth(160)
        self._cmb_concurrent_mode = NoWheelComboBox()
        self._cmb_concurrent_mode.setMinimumWidth(280)
        self._cmb_concurrent_mode.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self._cmb_concurrent_mode.addItem(
            "Désactivée — connexions illimitées par compte",
            CONCURRENT_SESSIONS_OFF,
        )
        self._cmb_concurrent_mode.addItem(
            "Un seul poste — un compte à la fois",
            CONCURRENT_SESSIONS_SINGLE,
        )
        self._cmb_concurrent_mode.addItem(
            "Limitée — nombre maximal de postes simultanés",
            CONCURRENT_SESSIONS_LIMITED,
        )
        self._spin_max_postes = NoWheelSpinBox()
        self._spin_max_postes.setRange(MIN_CONCURRENT_SESSIONS, MAX_CONCURRENT_SESSIONS)
        self._spin_max_postes.setValue(DEFAULT_MAX_CONCURRENT_SESSIONS)
        self._spin_max_postes.setMinimumWidth(160)
        for spin in (
            self._spin_timeout,
            self._spin_lock_attempts,
            self._spin_lock_minutes,
            self._spin_max_postes,
        ):
            spin.setMinimumHeight(32)
        self._cmb_concurrent_mode.setMinimumHeight(32)
        self._cmb_concurrent_mode.currentIndexChanged.connect(self._sync_concurrent_ui)
        self._btn_save_auth = QPushButton("Enregistrer")
        self._btn_save_auth.setObjectName("primaryButton")
        self._btn_save_auth.clicked.connect(self._save_auth_options)

        self._sessions_table = QTableWidget(0, 5)
        self._sessions_table.setHorizontalHeaderLabels(
            ["Utilisateur", "Poste", "Dernière activité", "Expire", "Actions"]
        )
        self._sessions_table.horizontalHeader().setStretchLastSection(True)
        self._sessions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._sessions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._sessions_table.setAlternatingRowColors(True)
        self._btn_refresh_sessions = QPushButton("Actualiser")
        self._btn_refresh_sessions.clicked.connect(self._reload_active_sessions)

        def _field_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setObjectName("usersAuthFormLabel")
            lbl.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            lbl.setMinimumWidth(200)
            return lbl

        def _hint(text: str, *, rich: bool = False) -> QLabel:
            h = QLabel(text)
            h.setObjectName("settingsPathsHint")
            h.setWordWrap(True)
            if rich:
                h.setTextFormat(Qt.TextFormat.RichText)
            return h

        def _section(title: str) -> tuple[QGroupBox, QVBoxLayout]:
            box = QGroupBox(title)
            box.setObjectName("usersAuthSection")
            box_lay = QVBoxLayout(box)
            box_lay.setSpacing(10)
            box_lay.setContentsMargins(14, 20, 14, 14)
            return box, box_lay

        def _grid_fields(parent: QVBoxLayout) -> QGridLayout:
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(12)
            grid.setColumnStretch(1, 1)
            parent.addLayout(grid)
            return grid

        intro = QLabel(
            "<b>Authentification</b> — si elle est désactivée, l’application s’ouvre sans identifiant. "
            "Si elle est activée, chaque compte n’accède qu’aux pages autorisées par son rôle."
        )
        intro.setWordWrap(True)
        intro.setObjectName("usersAuthIntro")
        intro.setTextFormat(Qt.TextFormat.RichText)

        g_activation, lay_act = _section("Activation")
        lay_act.addWidget(intro)
        lay_act.addSpacing(4)
        lay_act.addWidget(self._chk_enabled)
        lay_act.addWidget(self._chk_remember_user)

        g_session, lay_sess = _section("Durée de session")
        grid_sess = _grid_fields(lay_sess)
        grid_sess.addWidget(_field_label("Inactivité maximale :"), 0, 0)
        grid_sess.addWidget(self._spin_timeout, 0, 1, Qt.AlignmentFlag.AlignLeft)
        grid_sess.addWidget(
            _hint(
                f"Entre {MIN_SESSION_TIMEOUT_MINUTES} min et {MAX_SESSION_TIMEOUT_MINUTES // 60} h. "
                "À l’expiration, l’écran se verrouille (reconnexion ou mot de passe)."
            ),
            1,
            0,
            1,
            2,
        )

        g_lockout, lay_lock = _section("Verrouillage après échecs")
        grid_lock = _grid_fields(lay_lock)
        grid_lock.addWidget(_field_label("Tentatives autorisées :"), 0, 0)
        grid_lock.addWidget(self._spin_lock_attempts, 0, 1, Qt.AlignmentFlag.AlignLeft)
        grid_lock.addWidget(_field_label("Durée du blocage :"), 1, 0)
        grid_lock.addWidget(self._spin_lock_minutes, 1, 1, Qt.AlignmentFlag.AlignLeft)
        grid_lock.addWidget(
            _hint(
                f"Plages : {MIN_MAX_FAILED_ATTEMPTS}–{MAX_MAX_FAILED_ATTEMPTS} tentatives, "
                f"{MIN_LOCKOUT_MINUTES}–{MAX_LOCKOUT_MINUTES} minutes de verrouillage du compte."
            ),
            2,
            0,
            1,
            2,
        )

        g_concurrent, lay_conc = _section("Connexions simultanées")
        grid_conc = _grid_fields(lay_conc)
        grid_conc.addWidget(_field_label("Politique :"), 0, 0, Qt.AlignmentFlag.AlignTop)
        grid_conc.addWidget(self._cmb_concurrent_mode, 0, 1)
        postes_row = QHBoxLayout()
        postes_row.setSpacing(10)
        postes_row.addWidget(self._spin_max_postes)
        postes_row.addWidget(_hint("postes au maximum (mode limité)"))
        postes_row.addStretch(1)
        postes_host = QWidget()
        postes_host.setLayout(postes_row)
        grid_conc.addWidget(_field_label("Plafond :"), 1, 0)
        grid_conc.addWidget(postes_host, 1, 1)
        grid_conc.addWidget(
            _hint(
                "Nécessite MySQL (<b>auth_active_session</b>). Limite la réutilisation du même "
                "compte sur plusieurs postes. Une reconnexion sur le même poste remplace la session locale.",
                rich=True,
            ),
            2,
            0,
            1,
            2,
        )

        row_cards = QHBoxLayout()
        row_cards.setSpacing(14)
        row_cards.addWidget(g_session, stretch=1)
        row_cards.addWidget(g_lockout, stretch=1)

        save_bar = QFrame()
        save_bar.setObjectName("usersAuthSaveBar")
        save_bar_lay = QHBoxLayout(save_bar)
        save_bar_lay.setContentsMargins(0, 8, 0, 0)
        save_bar_lay.addWidget(self._btn_save_auth)
        save_bar_lay.addStretch(1)

        config_host = QWidget()
        config_host.setObjectName("usersAuthConfigHost")
        config_lay = QVBoxLayout(config_host)
        config_lay.setContentsMargins(0, 0, 4, 0)
        config_lay.setSpacing(14)
        config_lay.addWidget(g_activation)
        config_lay.addLayout(row_cards)
        config_lay.addWidget(g_concurrent)
        config_lay.addWidget(save_bar)
        config_lay.addStretch(0)

        scroll = QScrollArea()
        scroll.setObjectName("usersAuthConfigScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(config_host)
        scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(scroll)
        return tab

    def _build_sessions_tab(self) -> QWidget:
        g_sessions = QGroupBox("Sessions actives (postes connectés)")
        g_sessions.setObjectName("settingsPathsGroup")
        sess_lay = QVBoxLayout(g_sessions)
        sess_lay.setContentsMargins(12, 16, 12, 12)
        sess_lay.setSpacing(10)
        sess_header = QHBoxLayout()
        sess_hint = QLabel("Postes connectés — déconnexion à distance possible.")
        sess_hint.setObjectName("settingsPathsHint")
        sess_hint.setWordWrap(True)
        sess_header.addWidget(sess_hint, stretch=1)
        sess_header.addWidget(self._btn_refresh_sessions)
        sess_lay.addLayout(sess_header)
        self._lbl_current_poste = QLabel("")
        self._lbl_current_poste.setObjectName("usersAuthCurrentPoste")
        self._lbl_current_poste.setWordWrap(True)
        self._lbl_current_poste.setTextFormat(Qt.TextFormat.RichText)
        sess_lay.addWidget(self._lbl_current_poste)
        self._sessions_table.setMinimumHeight(140)
        sess_lay.addWidget(self._sessions_table, stretch=1)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(12)
        lay.addWidget(g_sessions, stretch=1)
        return tab

    def _sync_concurrent_ui(self) -> None:
        limited = self._cmb_concurrent_mode.currentData() == CONCURRENT_SESSIONS_LIMITED
        self._spin_max_postes.setEnabled(limited)

    def _build_roles_tab(self) -> QWidget:
        self._role_list = QListWidget()
        self._role_list.setMinimumWidth(220)
        self._role_list.currentRowChanged.connect(self._on_role_selected)
        self._role_name = QLineEdit()
        self._role_desc = QLineEdit()
        self._role_desc.setPlaceholderText("Description du rôle (optionnel)")
        self._role_badge = QLabel()
        self._role_badge.setObjectName("usersRoleBadge")
        self._role_lock_hint = QLabel()
        self._role_lock_hint.setObjectName("settingsPathsHint")
        self._role_lock_hint.setWordWrap(True)
        self._perm_scroll = QScrollArea()
        self._perm_scroll.setWidgetResizable(True)
        self._perm_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._perm_host = QWidget()
        self._perm_layout = QVBoxLayout(self._perm_host)
        self._perm_layout.setContentsMargins(4, 4, 4, 4)
        self._perm_scroll.setWidget(self._perm_host)
        self._btn_save_role = QPushButton("Enregistrer")
        self._btn_save_role.setObjectName("primaryButton")
        self._btn_save_role.clicked.connect(self._save_current_role)
        self._btn_add_role = QPushButton("Nouveau rôle…")
        self._btn_add_role.clicked.connect(self._add_role)
        self._btn_del_role = QPushButton("Supprimer")
        self._btn_del_role.clicked.connect(self._delete_role)

        g_roles = QGroupBox("Rôles")
        g_roles.setObjectName("settingsPathsGroup")
        role_left = QVBoxLayout(g_roles)
        role_left.setSpacing(10)
        role_left.addWidget(self._role_list, stretch=1)
        role_btn = QHBoxLayout()
        role_btn.addWidget(self._btn_add_role)
        role_btn.addWidget(self._btn_del_role)
        role_left.addLayout(role_btn)

        g_perms = QGroupBox("Permissions du rôle")
        g_perms.setObjectName("settingsPathsGroup")
        role_right = QVBoxLayout(g_perms)
        role_right.setSpacing(10)
        role_form = QFormLayout()
        role_form.setSpacing(10)
        role_form.addRow("Nom :", self._role_name)
        role_form.addRow("Description :", self._role_desc)
        role_right.addLayout(role_form)
        role_right.addWidget(self._role_badge)
        role_right.addWidget(self._role_lock_hint)
        role_right.addWidget(self._perm_scroll, stretch=1)
        role_right.addWidget(self._btn_save_role, alignment=Qt.AlignmentFlag.AlignRight)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(g_roles)
        split.addWidget(g_perms)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)

        tab = QWidget()
        lay = QHBoxLayout(tab)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(split)
        return tab

    def _build_users_tab(self) -> QWidget:
        self._user_search = QLineEdit()
        self._user_search.setPlaceholderText("Rechercher…")
        self._user_search.setClearButtonEnabled(True)
        self._user_search.textChanged.connect(self._filter_user_table)

        self._user_table = QTableWidget(0, 4)
        self._user_table.setHorizontalHeaderLabels(["Identifiant", "Nom affiché", "Rôle", "État"])
        hdr = self._user_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._user_table.verticalHeader().setVisible(False)
        self._user_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._user_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._user_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._user_table.setAlternatingRowColors(True)
        self._user_table.currentCellChanged.connect(self._on_user_row_changed)

        self._detail_title = QLabel("Sélectionnez un compte")
        self._detail_title.setObjectName("usersDetailTitle")
        self._detail_subtitle = QLabel("")
        self._detail_subtitle.setObjectName("usersDetailSubtitle")
        self._detail_state_badge = QLabel("")
        self._detail_state_badge.setObjectName("usersDetailStateBadge")
        self._detail_state_badge.hide()

        self._lbl_display = QLabel("Nom affiché")
        self._lbl_display.setObjectName("usersDetailFormLabel")
        self._detail_display = QLineEdit()
        self._detail_display.setPlaceholderText("Nom affiché dans l'application")
        self._detail_display.setMinimumHeight(34)
        self._lbl_role = QLabel("Rôle")
        self._lbl_role.setObjectName("usersDetailFormLabel")
        self._detail_role = QComboBox()
        self._detail_role.setMinimumHeight(34)
        self._detail_disabled = QCheckBox("Compte désactivé")
        self._detail_disabled.setToolTip("Un compte désactivé ne peut plus se connecter.")

        self._detail_created = QLabel("—")
        self._detail_last_login = QLabel("—")
        self._detail_pwd_changed = QLabel("—")
        for meta_lbl in (self._detail_created, self._detail_last_login, self._detail_pwd_changed):
            meta_lbl.setObjectName("usersDetailMetaValue")
            meta_lbl.setWordWrap(True)

        self._btn_save_user = QPushButton("Enregistrer")
        self._btn_save_user.setObjectName("primaryButton")
        self._btn_save_user.clicked.connect(self._save_selected_user)
        self._btn_rename_user = QPushButton("Renommer…")
        self._btn_rename_user.clicked.connect(self._rename_user)
        self._btn_pwd = QPushButton("Mot de passe…")
        self._btn_pwd.clicked.connect(self._change_password)
        self._btn_toggle_lock = QPushButton("Verrouiller")
        self._btn_toggle_lock.clicked.connect(self._toggle_user_lock)
        self._btn_revoke_all_sessions = QPushButton("Déconnecter tous les postes…")
        self._btn_revoke_all_sessions.setToolTip(
            "Clôturer toutes les sessions actives de ce compte sur les autres postes."
        )
        self._btn_revoke_all_sessions.clicked.connect(self._revoke_all_user_sessions)
        self._btn_del_user = QPushButton("Supprimer")
        self._btn_del_user.setObjectName("dangerButton")
        self._btn_del_user.clicked.connect(self._delete_user)
        self._btn_init_admin = QPushButton("Créer le compte administrateur initial…")
        self._btn_init_admin.clicked.connect(self._init_admin)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_text.addWidget(self._detail_title)
        header_text.addWidget(self._detail_subtitle)
        header_row.addLayout(header_text, stretch=1)
        header_row.addWidget(self._detail_state_badge, alignment=Qt.AlignmentFlag.AlignTop)

        profile_heading = QLabel("Profil")
        profile_heading.setObjectName("usersDetailSectionHeading")

        profile_form = QGridLayout()
        profile_form.setHorizontalSpacing(12)
        profile_form.setVerticalSpacing(10)
        profile_form.setColumnStretch(1, 1)
        profile_form.addWidget(profile_heading, 0, 0, 1, 2)
        profile_form.addWidget(self._lbl_display, 1, 0, 1, 2)
        profile_form.addWidget(self._detail_display, 2, 0, 1, 2)
        profile_form.addWidget(self._lbl_role, 3, 0, 1, 2)
        profile_form.addWidget(self._detail_role, 4, 0, 1, 2)
        profile_form.addWidget(self._detail_disabled, 5, 0, 1, 2)

        meta_heading = QLabel("Historique")
        meta_heading.setObjectName("usersDetailSectionHeading")
        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(12)
        meta_grid.setVerticalSpacing(8)
        meta_grid.setColumnStretch(1, 1)
        row = 0
        meta_grid.addWidget(meta_heading, row, 0, 1, 2)
        row += 1
        for label_text, value_widget in (
            ("Créé le", self._detail_created),
            ("Dernière connexion", self._detail_last_login),
            ("Mot de passe changé", self._detail_pwd_changed),
        ):
            lbl = QLabel(label_text)
            lbl.setObjectName("usersDetailMetaLabel")
            meta_grid.addWidget(lbl, row, 0, alignment=Qt.AlignmentFlag.AlignTop)
            meta_grid.addWidget(value_widget, row, 1)
            row += 1

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        actions_row.addWidget(self._btn_save_user)
        actions_row.addStretch()
        more = QPushButton("Actions du compte…")
        menu = QMenu(more)
        for button in (self._btn_rename_user, self._btn_pwd, self._btn_toggle_lock,
                       self._btn_revoke_all_sessions, self._btn_del_user):
            action = menu.addAction(button.text())
            action.triggered.connect(button.click)
            action.setProperty("source_button", button)
            button.hide()
        def sync_actions():
            for action in menu.actions():
                source = action.property("source_button")
                action.setText(source.text())
                action.setEnabled(source.isEnabled())
                action.setToolTip(source.toolTip())
        menu.aboutToShow.connect(sync_actions)
        more.setMenu(menu)
        actions_row.addWidget(more)

        detail_body = QWidget()
        detail_body.setObjectName("usersDetailBody")
        body_lay = QVBoxLayout(detail_body)
        body_lay.setContentsMargins(4, 4, 4, 8)
        body_lay.setSpacing(16)
        body_lay.addLayout(header_row)
        body_lay.addLayout(profile_form)
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setObjectName("usersDetailSeparator")
        body_lay.addWidget(sep1)
        body_lay.addLayout(meta_grid)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setObjectName("usersDetailSeparator")
        body_lay.addWidget(sep2)

        detail_scroll = _AccountDetailScroll()
        detail_scroll.setObjectName("usersDetailScroll")
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        detail_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        detail_scroll.setWidget(detail_body)
        detail_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        actions_host = QWidget()
        actions_host.setLayout(actions_row)

        g_detail = QGroupBox("Compte sélectionné")
        g_detail.setObjectName("usersDetailGroup")
        detail_lay = QVBoxLayout(g_detail)
        detail_lay.setContentsMargins(12, 16, 12, 12)
        detail_lay.setSpacing(8)
        detail_lay.addWidget(detail_scroll, stretch=1)
        detail_lay.addWidget(actions_host)

        g_accounts = QGroupBox("Comptes existants")
        g_accounts.setObjectName("settingsPathsGroup")
        accounts_lay = QVBoxLayout(g_accounts)
        accounts_lay.setSpacing(10)
        accounts_lay.addWidget(self._user_search)
        accounts_lay.addWidget(self._user_table, stretch=1)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(g_accounts)
        split.addWidget(g_detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setChildrenCollapsible(False)
        split.setSizes([620, 420])

        self._new_user = QLineEdit()
        self._new_user.setPlaceholderText("Identifiant")
        self._new_pass = QLineEdit()
        self._new_pass.setPlaceholderText(f"Mot de passe ({MIN_PASSWORD_LENGTH}+ car.)")
        self._new_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_pass2 = QLineEdit()
        self._new_pass2.setPlaceholderText("Confirmer le mot de passe")
        self._new_pass2.setEchoMode(QLineEdit.EchoMode.Password)
        self._new_display = QLineEdit()
        self._new_display.setPlaceholderText("Nom affiché")
        self._new_role = QComboBox()
        self._btn_add_user = QPushButton("Créer le compte")
        self._btn_add_user.setObjectName("primaryButton")
        self._btn_add_user.clicked.connect(self._add_user)

        add_form = QFormLayout()
        add_form.setSpacing(10)
        add_form.addRow("Identifiant :", self._new_user)
        add_form.addRow("Nom affiché :", self._new_display)
        add_form.addRow("Rôle :", self._new_role)
        add_form.addRow("Mot de passe :", self._new_pass)
        add_form.addRow("Confirmation :", self._new_pass2)

        g_add = QGroupBox("Nouveau compte")
        g_add.setObjectName("settingsPathsGroup")
        add_lay = QVBoxLayout(g_add)
        add_lay.addLayout(add_form)
        add_row = QHBoxLayout()
        add_row.addWidget(self._btn_add_user)
        add_row.addWidget(self._btn_init_admin)
        add_row.addStretch()
        add_lay.addLayout(add_row)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)
        self._btn_new_account = QPushButton("Nouveau compte…")
        self._btn_new_account.setCheckable(True)
        self._btn_new_account.toggled.connect(g_add.setVisible)
        g_add.hide()
        split.setMinimumHeight(300)
        lay.addWidget(self._btn_new_account, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(split, stretch=1)
        lay.addWidget(g_add)
        return tab

    def _build_log_tab(self) -> QWidget:
        self._log_request = 0
        self._log_busy = False
        self._log_pending = False
        self._log_lines = []
        self._log_timer = QTimer(self)
        self._log_timer.setSingleShot(True)
        self._log_timer.setInterval(300)
        self._log_timer.timeout.connect(self._reload_activity_log_async)
        self._log_filter_actor = QLineEdit()
        self._log_filter_actor.setPlaceholderText("Acteur…")
        self._log_filter_actor.setClearButtonEnabled(True)
        self._log_filter_actor.setMinimumWidth(120)

        self._log_filter_action = NoWheelComboBox()
        self._log_filter_action.setMinimumWidth(200)
        self._log_filter_action.addItem("Toutes les actions", "")
        for code, label in AUTH_JOURNAL_ACTION_LABELS:
            self._log_filter_action.addItem(label, code)
        self._log_filter_action.setToolTip("Filtrer par type d'événement")
        self._log_filter_action.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._log_filter_action.setMinimumContentsLength(22)

        self._log_filter_text = QLineEdit()
        self._log_filter_text.setPlaceholderText("Recherche dans le détail…")
        self._log_filter_text.setClearButtonEnabled(True)

        self._log_date_from = QDateEdit()
        self._log_date_from.setCalendarPopup(True)
        self._log_date_from.setDisplayFormat("dd/MM/yyyy")
        self._log_date_from.setSpecialValueText("—")
        self._log_date_from.setMinimumDate(QDate(2000, 1, 1))
        self._log_date_to = QDateEdit()
        self._log_date_to.setCalendarPopup(True)
        self._log_date_to.setDisplayFormat("dd/MM/yyyy")
        self._log_date_to.setSpecialValueText("—")
        self._log_date_to.setMinimumDate(QDate(2000, 1, 1))

        self._btn_search_log = QPushButton("Filtrer")
        self._btn_search_log.setObjectName("primaryButton")
        self._btn_search_log.clicked.connect(self._reload_activity_log_async)
        self._btn_refresh_log = QPushButton("Actualiser")
        self._btn_refresh_log.clicked.connect(self._reload_activity_log_async)
        self._btn_export_log = QPushButton("Exporter CSV…")
        self._btn_export_log.clicked.connect(self._export_activity_log)
        self._btn_clear_log_filters = QPushButton("Réinitialiser")
        self._btn_clear_log_filters.setToolTip("Effacer tous les filtres")
        self._btn_clear_log_filters.clicked.connect(self._clear_log_filters)

        self._lbl_log_count = QLabel("")
        self._lbl_log_count.setObjectName("settingsPathsHint")

        self._activity_table = QTableWidget(0, 5)
        self._activity_table.setHorizontalHeaderLabels(
            ["Date locale", "Acteur", "Action", "Module", "Détail"]
        )
        hdr = self._activity_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._activity_table.verticalHeader().setVisible(False)
        self._activity_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._activity_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._activity_table.setAlternatingRowColors(True)
        self._activity_table.setWordWrap(False)
        self._activity_table.setMinimumHeight(280)
        self._activity_table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        # Dates on their own row: long action labels must not widen every tab.
        filters = QFrame()
        filters.setObjectName("usersAuthLogFilters")
        filters_lay = QVBoxLayout(filters)
        filters_lay.setContentsMargins(10, 10, 10, 10)
        filters_lay.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel("Type d'action :"))
        row1.addWidget(self._log_filter_action, stretch=1)
        row1.addWidget(QLabel("Acteur :"))
        row1.addWidget(self._log_filter_actor, stretch=1)
        row_dates = QHBoxLayout()
        row_dates.addWidget(QLabel("Du :"))
        row_dates.addWidget(self._log_date_from)
        row_dates.addWidget(QLabel("Au :"))
        row_dates.addWidget(self._log_date_to)
        row_dates.addStretch()

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("Détail :"))
        row2.addWidget(self._log_filter_text, stretch=1)
        row2.addWidget(self._btn_search_log)
        row2.addWidget(self._btn_refresh_log)
        row2.addWidget(self._btn_clear_log_filters)
        row2.addWidget(self._btn_export_log)

        for field in (self._log_filter_actor, self._log_filter_text):
            field.textChanged.connect(self._schedule_log_search)
            field.returnPressed.connect(self._reload_activity_log_async)
        self._log_filter_action.currentIndexChanged.connect(self._schedule_log_search)
        self._log_date_from.dateChanged.connect(self._schedule_log_search)
        self._log_date_to.dateChanged.connect(self._schedule_log_search)

        intro = QLabel(
            "Connexions et modifications de comptes — journal MySQL centralisé (tous les postes)."
        )
        intro.setObjectName("settingsPathsHint")
        intro.setWordWrap(True)

        filters_lay.addWidget(intro)
        filters_lay.addLayout(row1)
        filters_lay.addLayout(row_dates)
        filters_lay.addLayout(row2)

        g_log = QGroupBox("Journal d'activité")
        g_log.setObjectName("settingsPathsGroup")
        g_log.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        log_lay = QVBoxLayout(g_log)
        log_lay.setContentsMargins(12, 16, 12, 12)
        log_lay.setSpacing(8)
        log_lay.addWidget(filters)
        log_lay.addWidget(self._lbl_log_count)
        log_lay.addWidget(self._activity_table, stretch=1)

        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(0)
        lay.addWidget(g_log, stretch=1)
        return tab

    def _clear_log_filters(self) -> None:
        self._log_filter_actor.clear()
        self._log_filter_action.setCurrentIndex(0)
        self._log_filter_text.clear()
        self._log_date_from.setDate(QDate(2000, 1, 1))
        self._log_date_to.setDate(QDate(2000, 1, 1))
        self._reload_activity_log_async()

    def mark_view_stale(self) -> None:
        self._view_data.mark_stale()
        self._invalidate_log_results()

    def _invalidate_log_results(self) -> None:
        self._log_request += 1
        self._log_loaded = False
        self._log_lines = []
        self._activity_table.setRowCount(0)
        self._btn_export_log.setEnabled(False)

    def _schedule_log_search(self, *_args) -> None:
        self._invalidate_log_results()
        self._log_pending = False
        self._lbl_log_count.setText("Filtres modifiés — recherche en attente…")
        self._log_timer.start()

    def mark_dirty(self) -> None:
        self.mark_view_stale()

    def on_page_activated(self) -> None:
        if self._view_data.needs_load() and not self._reload_busy and not self.has_unsaved_changes():
            self.reload()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def _on_tab_changed(self, index: int) -> None:
        if index == 3:
            self._reload_active_sessions()
        if index == 4 and not self._log_loaded:
            self._reload_activity_log_async()

    def _reload_active_sessions(self) -> None:
        if self._sessions_busy:
            return
        self._sessions_table.clearSpans()
        self._sessions_table.clearContents()
        if not session_can_manage_auth_settings():
            self._sessions_table.setRowCount(0)
            self._lbl_current_poste.setText("")
            return
        auth = deepcopy(self._auth)
        self._sessions_busy = True
        self._btn_refresh_sessions.setEnabled(False)
        self._lbl_current_poste.setText("Vérification des sessions en cours…")
        def work():
            auth.ensure_active_session_registered()
            return auth.local_session_context(), auth.list_active_sessions()
        def done(payload):
            self._sessions_busy = False
            self._btn_refresh_sessions.setEnabled(session_can_manage_auth_settings())
            if session_can_manage_auth_settings():
                self._apply_active_sessions(payload)
            else:
                self._sessions_table.setRowCount(0)
                self._lbl_current_poste.setText("")
        def failed(exc):
            self._sessions_busy = False
            self._btn_refresh_sessions.setEnabled(session_can_manage_auth_settings())
            self._sessions_table.setRowCount(1)
            self._sessions_table.setItem(0, 0, QTableWidgetItem(f"Contrôle de session impossible : {exc}"))
            self._lbl_current_poste.setText("Session non confirmée ; reconnectez-vous.")
        self._sessions_worker = run_in_background(self, work, on_success=done, on_error=failed)

    def _apply_active_sessions(self, payload):
        (cur_tok, _cur_cid, cur_machine), rows = payload
        session = self._auth.current_session()
        if session is not None and self._auth.is_enforced():
            user_lbl = session.display_name or session.username
            self._lbl_current_poste.setText(
                f"<b>Poste actuel :</b> {cur_machine} "
                f"(connecté en tant que <b>{user_lbl}</b>)"
            )
        else:
            self._lbl_current_poste.setText(
                "<i>Aucune session locale — authentification désactivée ou non connecté.</i>"
            )
        if not rows:
            self._sessions_table.setRowCount(1)
            empty = QTableWidgetItem(
                "Aucune session active enregistrée dans cette base. "
                "Les sessions sont enregistrées lors de la connexion."
            )
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._sessions_table.setItem(0, 0, empty)
            self._sessions_table.setSpan(0, 0, 1, 5)
            return
        self._sessions_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            is_here = bool(cur_tok) and row.session_token == cur_tok
            poste = (row.machine_label or row.client_instance_id[:12] or "—").strip()
            if is_here:
                poste = f"{poste}  ★ ce poste"
            self._sessions_table.setItem(i, 0, QTableWidgetItem(row.username))
            poste_item = QTableWidgetItem(poste)
            if is_here:
                f = poste_item.font()
                f.setBold(True)
                poste_item.setFont(f)
            self._sessions_table.setItem(i, 1, poste_item)
            self._sessions_table.setItem(
                i, 2, QTableWidgetItem(_format_ts(row.last_seen_at.isoformat()))
            )
            self._sessions_table.setItem(
                i, 3, QTableWidgetItem(_format_ts(row.expires_at.isoformat()))
            )
            btn = QPushButton("Déconnecter")
            btn.setProperty("session_token", row.session_token)
            target = self._auth.get_user(row.username)
            protected = (target is None
                         or (is_super_admin_role(target.role) and not session_is_super_admin())
                         or (target.role == ROLE_ADMIN and not session_has_admin_role()))
            btn.setEnabled(not is_here and not protected)
            if is_here:
                btn.setToolTip("Impossible de déconnecter votre propre session depuis ce poste.")
            elif protected:
                btn.setToolTip("Votre rôle ne permet pas de déconnecter ce compte administrateur.")
            btn.clicked.connect(self._on_revoke_session_clicked)
            self._sessions_table.setCellWidget(i, 4, btn)
        self._sessions_table.resizeColumnsToContents()

    def _on_revoke_session_clicked(self) -> None:
        btn = self.sender()
        if not isinstance(btn, QPushButton):
            return
        tok = str(btn.property("session_token") or "")
        if not tok:
            return
        if not confirm_yes_no(
            self,
            "Clôturer la session",
            "Forcer la déconnexion de ce poste ?\n"
            "L'utilisateur devra se reconnecter (écran de verrouillage).",
            default_no=True,
        ):
            return
        try:
            self._perform_auth_change("revoke_active_session", tok)
        except ValueError as e:
            QMessageBox.warning(self, "Sessions", str(e))
            return
        self._reload_active_sessions()

    def _set_loading_ui(self, loading: bool) -> None:
        self._tabs.setEnabled(not loading)
        self._btn_save_auth.setEnabled(not loading)
        if loading:
            self._status_lbl.setText("Chargement…")

    @staticmethod
    def _fetch_reload_payload() -> _AuthReloadPayload:
        from sme_gend.config.auth_config import load_auth_config
        auth = AuthService.__new__(AuthService)
        auth._config = load_auth_config()
        return _AuthReloadPayload(cfg=auth.config, audit_lines=[],
                                  uses_default_password=auth.uses_default_admin_password())

    def _user_values(self):
        return (self._detail_display.text(), self._detail_role.currentData(),
                self._detail_disabled.isChecked())

    def _role_values(self):
        return (self._role_name.text(), self._role_desc.text(),
                tuple(sorted(self._collect_role_permissions())))

    def _auth_values(self):
        return (self._chk_enabled.isChecked(), self._chk_remember_user.isChecked(),
                self._spin_timeout.value(), self._spin_lock_attempts.value(),
                self._spin_lock_minutes.value(), self._cmb_concurrent_mode.currentData(),
                self._spin_max_postes.value())

    def _has_draft(self, section):
        baseline = getattr(self, f"_{section}_baseline", None)
        return baseline is not None and baseline != getattr(self, f"_{section}_values")()

    def has_unsaved_changes(self) -> bool:
        return (any(self._has_draft(s) for s in ("user", "role", "auth"))
                or any(w.text() for w in (self._new_user, self._new_display,
                                         self._new_pass, self._new_pass2)))

    def _confirm_discard(self, section=None):
        dirty = self._has_draft(section) if section else self.has_unsaved_changes()
        return not dirty or confirm_yes_no(
            self, "Modifications non enregistrées",
            "Abandonner les modifications non enregistrées ?\n\n"
            "Choisissez Non pour les conserver et les enregistrer avant de continuer.",
            default_no=True)

    def _perform_auth_change(self, method, *args, **kwargs):
        """Run SQL/hash/audit on an isolated service; adopt state on the GUI thread."""
        if self._write_busy:
            raise ValueError("Une modification de compte est déjà en cours.")
        isolated = AuthService.__new__(AuthService)
        isolated.__dict__.update(deepcopy(self._auth.__dict__))
        original = self._auth._session
        self._write_busy = True
        def work():
            try:
                return True, getattr(isolated, method)(*args, **kwargs)
            except Exception as exc:
                return False, exc
        try:
            outcome = run_with_progress(self, "Utilisateurs & droits", "Enregistrement en cours…", work)
            if outcome is None:
                raise ValueError("L'opération n'a pas pu être terminée.")
            ok, result = outcome
            if not ok:
                from sme_gend.services.mysql_health import format_mysql_error_for_user
                raise ValueError(str(result) if isinstance(result, ValueError)
                                 else format_mysql_error_for_user(result))
            for auth in (self._auth, get_app_auth()):
                if auth is None:
                    continue
                auth.replace_config(isolated.config)
                current = auth._session
                # A session revoked while the modal was open must stay revoked.
                if current is original or (current is not None and original is not None
                        and current.username == original.username
                        and current.session_token == original.session_token):
                    updated = deepcopy(isolated._session)
                    if updated is not None and current is not None:
                        updated = replace(updated, expires_at=max(updated.expires_at, current.expires_at))
                    auth._session = updated
                if not isolated._enforced:
                    auth._enforced = False
            return result
        finally:
            self._write_busy = False

    def reload(self) -> None:
        self._view_data.mark_stale()
        if self._reload_busy:
            return
        if not self._confirm_discard():
            return
        self._user_baseline = self._role_baseline = self._auth_baseline = None
        for field in (self._new_user, self._new_display, self._new_pass, self._new_pass2):
            field.clear()
        if not self._view_data.needs_load():
            return
        self._reload_busy = True
        self._set_loading_ui(True)
        self._reload_worker = run_in_background(
            self,
            self._fetch_reload_payload,
            self._apply_reload_payload,
            on_error=self._on_reload_error,
        )

    def _on_reload_error(self, exc: BaseException) -> None:
        self._reload_busy = False
        self._set_loading_ui(False)
        self._status_lbl.setText(f"<span style='color:#c62828'>Erreur : {exc}</span>")
        QMessageBox.critical(self, "Utilisateurs", str(exc))

    def _assignable_roles(self, cfg: AuthConfig):
        allow_super = session_is_super_admin()
        for role in cfg.roles:
            if is_super_admin_role(role.id) and not allow_super:
                continue
            if role.id == ROLE_ADMIN and not session_has_admin_role():
                continue
            yield role

    def _apply_reload_payload(self, payload: _AuthReloadPayload) -> None:
        prev_role = self._selected_role_id
        prev_user = self._selected_username()
        self._auth.replace_config(payload.cfg)
        shared = get_app_auth()
        if shared is not None and shared is not self._auth:
            shared.replace_config(payload.cfg)

        cfg = payload.cfg
        self._uses_default_password = payload.uses_default_password
        self._chk_enabled.setChecked(cfg.enabled)
        self._chk_remember_user.setChecked(cfg.remember_last_username)
        self._spin_timeout.setValue(cfg.session_timeout_minutes)
        self._spin_lock_attempts.setValue(cfg.max_failed_attempts)
        self._spin_lock_minutes.setValue(cfg.lockout_minutes)
        mode = getattr(cfg, "concurrent_sessions_mode", CONCURRENT_SESSIONS_OFF) or CONCURRENT_SESSIONS_OFF
        idx = max(0, self._cmb_concurrent_mode.findData(mode))
        self._cmb_concurrent_mode.setCurrentIndex(idx)
        self._spin_max_postes.setValue(
            int(getattr(cfg, "max_concurrent_sessions", DEFAULT_MAX_CONCURRENT_SESSIONS) or 2)
        )
        self._sync_concurrent_ui()
        self._auth_baseline = self._auth_values()
        self._btn_init_admin.setVisible(not cfg.users)
        if not cfg.users:
            self._btn_new_account.setChecked(True)

        self._rebuild_role_combo(cfg)
        self._rebuild_permission_checks()
        self._fill_role_list(cfg, prev_role)
        self._fill_user_table(cfg, prev_user)
        self._update_warning_banner()

        if self._tabs.currentIndex() == 3:
            self._reload_active_sessions()
        if self._tabs.currentIndex() == 4:
            self._reload_activity_log_async()

        n_users = len(cfg.users)
        n_roles = len(cfg.roles)
        auth_txt = "activée" if cfg.enabled else "désactivée"
        self._status_lbl.setText(
            f"{n_users} compte(s), {n_roles} rôle(s) — authentification {auth_txt}."
        )
        self._reload_busy = False
        self._view_data.finish_load()
        self._set_loading_ui(False)
        self._apply_tab_permissions()

    def _apply_tab_permissions(self) -> None:
        can_accounts = session_can_manage_user_accounts()
        can_view_accounts = session_can_view_user_accounts()
        can_roles = session_can_manage_user_roles()
        can_settings = session_can_manage_auth_settings()
        can_journal = session_can_view_auth_journal()
        can_delete = session_can_delete_user_accounts()

        self._tabs.setTabEnabled(0, can_view_accounts or can_accounts)
        self._tabs.setTabEnabled(1, can_roles)
        self._tabs.setTabEnabled(2, can_settings)
        self._tabs.setTabEnabled(3, can_settings)
        self._tabs.setTabEnabled(4, can_journal)

        read_only_accounts = can_view_accounts and not can_accounts
        self._btn_add_user.setEnabled(can_accounts)
        self._btn_init_admin.setEnabled(can_accounts)
        self._btn_new_account.setEnabled(can_accounts)
        self._btn_save_user.setEnabled(can_accounts and self._btn_save_user.isEnabled())
        self._btn_rename_user.setEnabled(can_accounts and self._btn_rename_user.isEnabled())
        self._btn_pwd.setEnabled(can_accounts and self._btn_pwd.isEnabled())
        self._btn_toggle_lock.setEnabled(can_accounts and self._btn_toggle_lock.isEnabled())
        self._btn_revoke_all_sessions.setEnabled(
            can_accounts and self._btn_revoke_all_sessions.isEnabled()
        )
        self._btn_del_user.setEnabled(can_delete and self._btn_del_user.isEnabled())
        self._new_user.setReadOnly(read_only_accounts)
        self._new_pass.setReadOnly(read_only_accounts)
        self._new_pass2.setReadOnly(read_only_accounts)
        self._new_display.setReadOnly(read_only_accounts)
        self._new_role.setEnabled(can_accounts)

        self._btn_save_role.setEnabled(can_roles)
        self._btn_add_role.setEnabled(can_roles)
        self._btn_del_role.setEnabled(can_roles)
        self._role_name.setReadOnly(not can_roles)
        self._role_desc.setReadOnly(not can_roles)
        rid = self._selected_role_id or ""
        self._btn_save_role.setEnabled(can_roles and bool(rid) and not is_super_admin_role(rid))
        self._btn_del_role.setEnabled(can_roles and bool(rid) and not is_system_protected_role(rid))
        self._role_name.setReadOnly(not can_roles or is_super_admin_role(rid))
        self._role_desc.setReadOnly(not can_roles or is_super_admin_role(rid))
        for pid, chk in self._role_perm_checks.items():
            chk.setEnabled(can_roles and bool(rid) and not role_permissions_locked(rid)
                           and role_may_grant_permission(rid, pid))

        auth_ro = not can_settings
        self._chk_enabled.setEnabled(not auth_ro and session_is_super_admin())
        self._chk_enabled.setToolTip(
            "Réservé au super administrateur. Désactiver la connexion supprime les contrôles "
            "d'accès ; l'activation s'applique au prochain démarrage des postes.")
        self._chk_remember_user.setEnabled(not auth_ro)
        self._spin_timeout.setReadOnly(auth_ro)
        self._spin_lock_attempts.setReadOnly(auth_ro)
        self._spin_lock_minutes.setReadOnly(auth_ro)
        self._cmb_concurrent_mode.setEnabled(not auth_ro)
        self._spin_max_postes.setReadOnly(auth_ro)
        self._btn_save_auth.setEnabled(can_settings)
        self._btn_refresh_sessions.setEnabled(can_settings and not self._sessions_busy)
        self._sessions_table.setEnabled(can_settings)

        if not can_journal:
            self._lbl_log_count.setText("Accès refusé au journal.")
            self._log_timer.stop()
            self._log_pending = False
            self._invalidate_log_results()
        journal_ro = not can_journal
        self._log_filter_actor.setReadOnly(journal_ro)
        self._log_filter_action.setEnabled(can_journal)
        self._log_filter_text.setReadOnly(journal_ro)
        self._log_date_from.setReadOnly(journal_ro)
        self._log_date_to.setReadOnly(journal_ro)
        self._btn_search_log.setEnabled(can_journal and not self._log_busy)
        self._btn_refresh_log.setEnabled(can_journal and not self._log_busy)
        self._btn_export_log.setEnabled(can_journal and self._log_loaded and not self._log_busy)
        self._btn_clear_log_filters.setEnabled(can_journal)

    def _update_warning_banner(self) -> None:
        if self._uses_default_password:
            self._warn_banner.setText(
                "Le compte <b>admin</b> utilise encore le mot de passe initial. Changez-le."
            )
            self._warn_banner.show()
        elif self._chk_enabled.isChecked() is False:
            self._warn_banner.setText(
                "Authentification <b>désactivée</b> : accès libre à l’ouverture."
            )
            self._warn_banner.show()
        else:
            self._warn_banner.hide()

    def _rebuild_role_combo(self, cfg: AuthConfig | None = None) -> None:
        cfg = cfg or self._auth.config
        current_new = self._new_role.currentData()
        current_detail = self._detail_role.currentData()
        self._new_role.clear()
        self._detail_role.clear()
        for role in self._assignable_roles(cfg):
            self._new_role.addItem(role.name, role.id)
            self._detail_role.addItem(role.name, role.id)
        self._select_combo_data(self._new_role, current_new or ROLE_OPERATEUR)
        self._select_combo_data(self._detail_role, current_detail)

    @staticmethod
    def _select_combo_data(combo: QComboBox, role_id: str | None) -> None:
        if not role_id:
            return
        idx = combo.findData(role_id)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _fill_role_list(self, cfg: AuthConfig, prev_role: str | None) -> None:
        self._role_list.blockSignals(True)
        self._role_list.clear()
        restore = 0
        for i, role in enumerate(cfg.roles):
            suffix = " · système" if is_system_protected_role(role.id) else ""
            item = QListWidgetItem(f"{role.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, role.id)
            item.setToolTip(role.description or "")
            self._role_list.addItem(item)
            if role.id == prev_role:
                restore = i
        self._role_list.blockSignals(False)
        if self._role_list.count():
            self._role_list.setCurrentRow(restore)
        else:
            self._selected_role_id = None

    def _fill_user_table(self, cfg: AuthConfig, prev_user: str | None) -> None:
        self._user_table.blockSignals(True)
        self._user_table.setRowCount(0)
        restore_row = 0
        session = self._auth.current_session()
        me = (session.username if session else "") or ""
        for u in cfg.users:
            row = self._user_table.rowCount()
            self._user_table.insertRow(row)
            ident = QTableWidgetItem(u.username)
            if me and u.username.casefold() == me.casefold():
                ident.setText(f"{u.username}  (vous)")
            ident.setData(Qt.ItemDataRole.UserRole, u.username)
            self._user_table.setItem(row, 0, ident)
            self._user_table.setItem(row, 1, QTableWidgetItem(u.display_name or u.username))
            role = cfg.role_by_id(u.role)
            role_item = QTableWidgetItem(role.name if role else u.role)
            role_item.setData(Qt.ItemDataRole.UserRole, u.role)
            self._user_table.setItem(row, 2, role_item)
            state = QTableWidgetItem(_lock_state_label(u))
            if getattr(u, "disabled", False):
                state.setForeground(Qt.GlobalColor.gray)
            elif _user_is_locked(u):
                state.setForeground(Qt.GlobalColor.darkRed)
            self._user_table.setItem(row, 3, state)
            if prev_user and u.username.casefold() == prev_user.casefold():
                restore_row = row
        self._user_table.blockSignals(False)
        self._filter_user_table(self._user_search.text())
        if self._user_table.rowCount():
            self._user_table.setCurrentCell(restore_row, 0)
            self._load_user_detail(restore_row)
        else:
            self._clear_user_detail()

    def _filter_user_table(self, text: str) -> None:
        needle = normalize_search_text((text or "").strip().casefold())
        for row in range(self._user_table.rowCount()):
            if not needle:
                self._user_table.setRowHidden(row, False)
                continue
            blob = normalize_search_text(" ".join(
                (self._user_table.item(row, c).text() if self._user_table.item(row, c) else "")
                for c in range(self._user_table.columnCount())
            ).casefold())
            self._user_table.setRowHidden(row, needle not in normalize_search_text(blob))

    def _selected_username(self) -> str | None:
        row = self._user_table.currentRow()
        if row < 0:
            return None
        item = self._user_table.item(row, 0)
        if item is None:
            return None
        raw = item.data(Qt.ItemDataRole.UserRole)
        return str(raw or item.text().split("  (vous)")[0]).strip() or None

    def _on_user_row_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        if not self._confirm_discard("user"):
            self._user_table.blockSignals(True)
            self._user_table.setCurrentCell(_prev_row, max(0, _prev_col))
            self._user_table.blockSignals(False)
            return
        self._load_user_detail(row)

    def _clear_user_detail(self) -> None:
        self._user_baseline = None
        self._detail_username = None
        self._detail_title.setText("Sélectionnez un compte")
        self._detail_subtitle.setText("Choisissez une ligne dans la liste.")
        self._detail_state_badge.setText("")
        self._detail_state_badge.hide()
        self._detail_display.clear()
        self._detail_disabled.setChecked(False)
        self._detail_created.setText("—")
        self._detail_last_login.setText("—")
        self._detail_pwd_changed.setText("—")
        self._detail_display.setEnabled(False)
        self._detail_role.setEnabled(False)
        self._detail_disabled.setEnabled(False)
        self._btn_save_user.setEnabled(False)
        self._btn_rename_user.setEnabled(False)
        self._btn_pwd.setEnabled(False)
        self._btn_toggle_lock.setEnabled(False)
        self._btn_revoke_all_sessions.setEnabled(False)
        self._btn_del_user.setEnabled(False)

    def _apply_detail_state_badge(self, user) -> None:
        state = _lock_state_label(user)
        self._detail_state_badge.setText(state)
        self._detail_state_badge.show()
        badge = self._detail_state_badge
        if getattr(user, "disabled", False):
            badge.setProperty("stateKind", "disabled")
        elif _user_is_locked(user):
            badge.setProperty("stateKind", "locked")
        else:
            badge.setProperty("stateKind", "ok")
        badge.style().unpolish(badge)
        badge.style().polish(badge)

    def _load_user_detail(self, row: int) -> None:
        if row < 0:
            self._clear_user_detail()
            return
        uname = self._selected_username()
        user = self._auth.get_user(uname or "") if uname else None
        if user is None:
            self._clear_user_detail()
            return
        self._detail_title.setText(user.username)
        self._detail_username = user.username
        role = self._auth.config.role_by_id(user.role)
        role_name = role.name if role else user.role
        self._detail_subtitle.setText(role_name)
        self._detail_display.setText(user.display_name or user.username)
        if self._detail_role.findData(user.role) < 0:
            role = self._auth.config.role_by_id(user.role)
            self._detail_role.insertItem(0, role.name if role else user.role, user.role)
        self._select_combo_data(self._detail_role, user.role)
        self._detail_disabled.setChecked(bool(user.disabled))
        self._apply_detail_state_badge(user)
        self._detail_created.setText(_format_ts(user.created_at))
        self._detail_last_login.setText(_format_ts(user.last_login_at))
        self._detail_pwd_changed.setText(_format_ts(user.password_changed_at))
        can_modify = session_can_manage_user_accounts()
        if is_super_admin_role(user.role) and not session_is_super_admin():
            can_modify = False
        if user.role == ROLE_ADMIN and not session_has_admin_role():
            can_modify = False
        can_view = session_can_view_user_accounts() or can_modify
        if not can_view:
            self._clear_user_detail()
            return
        self._detail_display.setEnabled(True)
        self._detail_display.setReadOnly(not can_modify)
        self._detail_role.setEnabled(can_modify)
        self._detail_disabled.setEnabled(can_modify)
        self._btn_save_user.setEnabled(can_modify)
        self._btn_pwd.setEnabled(can_modify)
        session = self._auth.current_session()
        is_self = bool(
            session and session.username.casefold() == user.username.casefold()
        )
        locked = _user_is_locked(user)
        if locked:
            self._btn_toggle_lock.setText("Déverrouiller")
            self._btn_toggle_lock.setToolTip("Rétablir la connexion de ce compte")
        else:
            self._btn_toggle_lock.setText("Verrouiller")
            self._btn_toggle_lock.setToolTip("Empêcher la connexion jusqu'à déverrouillage")
        self._btn_rename_user.setEnabled(can_modify and not is_self)
        self._btn_toggle_lock.setEnabled(can_modify and not is_self)
        self._btn_revoke_all_sessions.setEnabled(can_modify and not is_self)
        self._btn_del_user.setEnabled(
            session_can_delete_user_accounts() and can_modify and not is_self
        )
        self._apply_tab_permissions()
        self._user_baseline = self._user_values()

    def _rebuild_permission_checks(self) -> None:
        while self._perm_layout.count():
            item = self._perm_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._role_perm_checks.clear()
        current_group = ""
        catalog = self._auth.permission_catalog()
        group_rank = {}
        for perm in catalog:
            group_rank.setdefault(display_ui_title(perm.group), len(group_rank))
        catalog = sorted(catalog, key=lambda perm: (
            group_rank[display_ui_title(perm.group)], display_ui_title(perm.label).casefold()))
        for perm in catalog:
            group = display_ui_title(perm.group)
            if group != current_group:
                current_group = group
                gl = QLabel(f"<b>{current_group}</b>")
                self._perm_layout.addWidget(gl)
            chk = QCheckBox(display_ui_title(perm.label).replace("&", "&&"))
            desc = perm.description or ""
            desc = (desc.replace("Archives & clôture", MONTH_MANAGEMENT_TITLE)
                        .replace("Configurer →", ADMIN_TOOLS_TITLE + " →")
                        .replace("Personnel supprimé", REMOVED_RECORDS_TITLE))
            if perm.id in ADMIN_ONLY_PERMISSIONS:
                suffix = "Réservé administrateur et super administrateur."
                desc = f"{desc} {suffix}".strip() if desc else suffix
            chk.setToolTip(desc)
            chk.setProperty("perm_id", perm.id)
            self._role_perm_checks[perm.id] = chk
            self._perm_layout.addWidget(chk)
        self._perm_layout.addStretch()

    def _on_role_selected(self, row: int) -> None:
        if row < 0:
            self._selected_role_id = None
            return
        item = self._role_list.item(row)
        if not item:
            return
        role_id = str(item.data(Qt.ItemDataRole.UserRole))
        if (getattr(self, "_role_baseline", None) is not None
                and role_id != self._selected_role_id and not self._confirm_discard("role")):
            self._role_list.blockSignals(True)
            for i in range(self._role_list.count()):
                if self._role_list.item(i).data(Qt.ItemDataRole.UserRole) == self._selected_role_id:
                    self._role_list.setCurrentRow(i)
                    break
            self._role_list.blockSignals(False)
            return
        self._selected_role_id = role_id
        role = self._auth.config.role_by_id(role_id)
        if not role:
            return
        self._role_name.setText(role.name)
        self._role_desc.setText(role.description)
        locked = role_permissions_locked(role_id)
        protected = is_system_protected_role(role_id)
        super_locked = is_super_admin_role(role_id)
        if super_locked:
            self._role_badge.setText("Super administrateur — accès total")
        elif role_id == ROLE_ADMIN:
            self._role_badge.setText("Rôle système — administration")
        elif locked:
            self._role_badge.setText("Rôle système — permissions figées")
        elif protected:
            self._role_badge.setText("Rôle système — permissions personnalisables")
        else:
            self._role_badge.setText("Rôle personnalisé")
        if locked:
            if role_id == ROLE_ADMIN:
                self._role_lock_hint.setText(
                    "Le rôle <b>Administrateur</b> inclut toutes les permissions métier "
                    "et d'administration — profil fixe."
                )
            else:
                self._role_lock_hint.setText(
                    "Le super administrateur dispose de l'accès total — profil fixe."
                )
            self._role_lock_hint.show()
        else:
            self._role_lock_hint.setText(
                "Cochez les permissions puis cliquez <b>Enregistrer</b>. "
                "Les droits réservés administrateur ne peuvent pas être délégués ici."
            )
            self._role_lock_hint.show()

        all_access = "*" in role.permissions
        can_edit_roles = session_can_manage_user_roles()
        for pid, chk in self._role_perm_checks.items():
            chk.blockSignals(True)
            chk.setChecked(pid in role.permissions or all_access)
            grantable = role_may_grant_permission(role_id, pid)
            chk.setEnabled(not locked and grantable and can_edit_roles)
            chk.blockSignals(False)

        self._role_name.setReadOnly(super_locked or not can_edit_roles)
        self._role_desc.setReadOnly(super_locked or not can_edit_roles)
        self._btn_save_role.setEnabled(can_edit_roles and not super_locked)
        self._btn_del_role.setEnabled(can_edit_roles and not protected)
        if isinstance(self, UsersAuthView):
            self._role_baseline = self._role_values()
            self._role_expected = (role.name, role.description, tuple(sorted(role.permissions)))

    def _collect_role_permissions(self) -> list[str]:
        return [pid for pid, chk in self._role_perm_checks.items() if chk.isChecked()]

    def _save_auth_options(self) -> None:
        if (self._auth.config.enabled and not self._chk_enabled.isChecked()
                and not confirm_yes_no(self, "Désactiver la connexion",
                    "Désactiver l'authentification donne accès à toutes les fonctions sans connexion.\n\n"
                    "Ce poste perd immédiatement ses contrôles d'accès. Les autres postes prennent "
                    "en compte la configuration à leur prochain démarrage. Continuer ?", default_no=True)):
            self._chk_enabled.setChecked(True)
            return
        try:
            self._perform_auth_change("save_auth_settings",
                enabled=self._chk_enabled.isChecked(),
                session_timeout_minutes=self._spin_timeout.value(),
                remember_last_username=self._chk_remember_user.isChecked(),
                max_failed_attempts=self._spin_lock_attempts.value(),
                lockout_minutes=self._spin_lock_minutes.value(),
                concurrent_sessions_mode=self._cmb_concurrent_mode.currentData(),
                max_concurrent_sessions=self._spin_max_postes.value(),
            )
        except ValueError as e:
            QMessageBox.warning(self, "Authentification", str(e))
            self._chk_enabled.setChecked(self._auth.config.enabled)
            return
        self._auth_baseline = self._auth_values()
        if self._chk_enabled.isChecked():
            msg = (
                "Options enregistrées.\n\n"
                "L'écran de connexion sera obligatoire au prochain démarrage."
            )
        else:
            msg = (
                "Authentification désactivée : plus de connexion requise "
                "(session en cours fermée)."
            )
        QMessageBox.information(self, "Authentification", msg)
        self._update_warning_banner()
        self._status_lbl.setText(
            f"{len(self._auth.config.users)} compte(s) — authentification "
            f"{'activée' if self._auth.config.enabled else 'désactivée'}."
        )

    def _save_current_role(self) -> None:
        if not self._selected_role_id:
            QMessageBox.information(self, "Rôle", "Sélectionnez un rôle.")
            return
        perms = None if role_permissions_locked(self._selected_role_id) else self._collect_role_permissions()
        try:
            self._perform_auth_change("update_role",
                self._selected_role_id,
                name=self._role_name.text(),
                description=self._role_desc.text(),
                permissions=perms,
                expected_profile=self._role_expected,
            )
        except ValueError as e:
            QMessageBox.warning(self, "Rôle", str(e))
            return
        self._role_baseline = self._role_values()
        self.reload()
        QMessageBox.information(self, "Rôle", "Rôle enregistré.")

    def _add_role(self) -> None:
        dlg = _NewRoleDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        role_id, name, desc = dlg.values()
        if not role_id:
            return
        try:
            self._perform_auth_change("add_role", role_id, name or role_id, description=desc)
        except ValueError as e:
            QMessageBox.warning(self, "Rôle", str(e))
            return
        self.reload()

    def _delete_role(self) -> None:
        if not self._selected_role_id:
            return
        role = self._auth.config.role_by_id(self._selected_role_id)
        role_name = role.name if role else self._selected_role_id
        if not confirm_yes_no(
            self,
            "Supprimer le rôle",
            f"Supprimer le rôle « {role_name} » ?",
        ):
            return
        try:
            self._perform_auth_change("remove_role", self._selected_role_id)
        except ValueError as e:
            QMessageBox.warning(self, "Rôle", str(e))
            return
        self.reload()

    def _init_admin(self) -> None:
        dlg = _PasswordDialog("admin", self)
        dlg.setWindowTitle("Compte administrateur initial")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._perform_auth_change("ensure_default_admin", dlg.password())
        except ValueError as e:
            QMessageBox.warning(self, "Compte", str(e))
            return
        self.reload()
        QMessageBox.information(
            self,
            "Compte créé",
            "Compte « admin » créé (rôle super administrateur).",
        )

    def _reload_activity_log_async(self) -> None:
        from sme_gend.services.audit_log import search_auth_activity
        from sme_gend.repositories.app_config_repository import audit_date_bounds
        from sme_gend.config import get_settings
        self._log_timer.stop()
        self._invalidate_log_results()
        if not session_can_view_auth_journal():
            self._log_pending = False
            self._lbl_log_count.setText("Accès refusé au journal.")
            return
        date_from = self._log_date_from.date().toPython() if self._log_date_from.date() > QDate(2000, 1, 1) else None
        date_to = self._log_date_to.date().toPython() if self._log_date_to.date() > QDate(2000, 1, 1) else None
        try:
            audit_date_bounds(date_from, date_to)
        except ValueError as exc:
            self._log_pending = False
            self._lbl_log_count.setText(str(exc))
            return
        if self._log_busy:
            self._log_pending = True
            return
        try:
            settings = get_settings()
        except Exception:
            self._on_log_error(RuntimeError("Configuration indisponible."))
            return
        query = dict(actor=self._log_filter_actor.text().strip() or None,
            action=self._log_filter_action.currentData() or None,
            text=self._log_filter_text.text().strip() or None,
            date_from=date_from, date_to=date_to, limit=500, settings=settings)
        serial = self._log_request
        self._log_busy = True
        self._log_pending = False
        self._btn_refresh_log.setEnabled(False)
        self._btn_search_log.setEnabled(False)
        self._lbl_log_count.setText("Recherche dans le journal central…")
        def finish():
            self._log_busy = False
            self._apply_tab_permissions()
            if self._log_pending:
                self._log_pending = False
                self._log_timer.start(0)
        def done(lines):
            try:
                if not session_can_view_auth_journal():
                    self._invalidate_log_results()
                    self._lbl_log_count.setText("Accès refusé au journal.")
                elif serial == self._log_request:
                    self._apply_audit_lines(lines)
                    self._log_loaded = True
            finally:
                finish()
        def fail(exc):
            if serial == self._log_request:
                self._on_log_error(exc)
            finish()
        try:
            run_in_background(self, lambda: search_auth_activity(**query), done, on_error=fail)
        except Exception as exc:
            fail(exc)

    def _export_activity_log(self) -> None:
        from sme_gend.services.audit_log import export_auth_activity_csv

        if not session_can_view_auth_journal() or not self._log_loaded or not self._log_lines:
            QMessageBox.information(self, "Export", "Aucune ligne à exporter — filtrez d'abord le journal.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Exporter le journal auth",
            "journal_auth.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            csv_text = export_auth_activity_csv(self._log_lines)
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(csv_text)
        except OSError as exc:
            QMessageBox.warning(self, "Export", str(exc))
            return
        QMessageBox.information(self, "Export", f"Journal exporté :\n{path}")

    def _on_log_error(self, exc: BaseException) -> None:
        self._invalidate_log_results()
        self._lbl_log_count.setText("Lecture du journal central impossible. Vérifiez MySQL puis réessayez.")
        self._apply_tab_permissions()

    def _apply_audit_lines(self, lines: list[dict[str, Any]]) -> None:
        self._log_lines = list(lines)
        self._activity_table.setRowCount(0)
        if not lines:
            self._lbl_log_count.setText("Aucune entrée pour les filtres sélectionnés.")
            self._activity_table.insertRow(0)
            empty = QTableWidgetItem("Aucune entrée pour le moment.")
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self._activity_table.setItem(0, 4, empty)
            return
        self._lbl_log_count.setText(f"{len(lines)} événement(s) affiché(s)")
        for e in lines:
            row = self._activity_table.rowCount()
            self._activity_table.insertRow(row)
            raw_ts = str(e.get("ts_utc") or "")
            try:
                timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                ts = timestamp.astimezone().strftime("%d/%m/%Y %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                ts = raw_ts or "—"
            detail = str(e.get("detail", "") or "")
            if not detail and isinstance(e.get("payload"), dict):
                detail = str(e["payload"].get("detail") or "")
            action_code = str(e.get("action", "") or "")
            values = (
                ts,
                str(e.get("actor", "?")),
                _auth_action_label(action_code),
                str(e.get("module", "")),
                detail,
            )
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col == 2 and action_code:
                    item.setToolTip(action_code)
                self._activity_table.setItem(row, col, item)

    def _add_user(self) -> None:
        if self._new_pass.text() != self._new_pass2.text():
            QMessageBox.warning(self, "Utilisateur", "Les deux mots de passe ne correspondent pas.")
            return
        role_id = self._new_role.currentData()
        try:
            self._perform_auth_change("add_user",
                self._new_user.text(),
                self._new_pass.text(),
                display_name=self._new_display.text(),
                role=str(role_id or "operateur"),
            )
        except ValueError as e:
            QMessageBox.warning(self, "Utilisateur", str(e))
            return
        self._new_user.clear()
        self._new_pass.clear()
        self._new_pass2.clear()
        self._new_display.clear()
        self.reload()

    def _save_selected_user(self) -> None:
        uname = self._selected_username()
        if not uname:
            QMessageBox.information(self, "Compte", "Sélectionnez un utilisateur.")
            return
        role_id = str(self._detail_role.currentData() or "operateur")
        try:
            self._perform_auth_change("update_user",
                uname,
                display_name=self._detail_display.text(),
                role=role_id,
                disabled=self._detail_disabled.isChecked(),
                expected_profile=self._user_baseline,
            )
        except ValueError as e:
            QMessageBox.warning(self, "Compte", str(e))
            return
        self._user_baseline = self._user_values()
        self.reload()
        QMessageBox.information(self, "Compte", "Compte mis à jour.")

    def _rename_user(self) -> None:
        uname = self._selected_username()
        if not uname:
            return
        dlg = _RenameUserDialog(uname, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_name = dlg.new_username()
        try:
            self._perform_auth_change("rename_user", uname, new_name)
        except ValueError as e:
            QMessageBox.warning(self, "Renommage", str(e))
            return
        self.reload()
        QMessageBox.information(
            self,
            "Renommage",
            f"Identifiant renommé : « {uname} » → « {new_name} ».",
        )

    def _delete_user(self) -> None:
        username = self._selected_username()
        if not username:
            return
        if not confirm_yes_no(
            self,
            "Supprimer l'utilisateur",
            f"Supprimer le compte « {username} » ?\n\n"
            "Cette personne perdra immédiatement l'accès à l'application.",
        ):
            return
        try:
            self._perform_auth_change("remove_user", username)
        except ValueError as e:
            QMessageBox.warning(self, "Suppression", str(e))
            return
        self.reload()

    def _change_password(self) -> None:
        uname = self._selected_username()
        if not uname:
            QMessageBox.information(self, "Mot de passe", "Sélectionnez un utilisateur.")
            return
        dlg = _PasswordDialog(uname, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._perform_auth_change("change_password", uname, dlg.password())
        except ValueError as e:
            QMessageBox.warning(self, "Mot de passe", str(e))
            return
        QMessageBox.information(self, "Mot de passe", "Mot de passe mis à jour.")
        self.reload()

    def _toggle_user_lock(self) -> None:
        uname = self._selected_username()
        if not uname:
            return
        user = self._auth.get_user(uname)
        if user is None:
            return
        if _user_is_locked(user):
            try:
                self._perform_auth_change("unlock_user", uname)
            except ValueError as e:
                QMessageBox.warning(self, "Compte", str(e))
                return
            self.reload()
            QMessageBox.information(self, "Compte", f"Le compte « {uname} » est déverrouillé.")
            return
        if not confirm_yes_no(
            self,
            "Verrouiller le compte",
            f"Verrouiller le compte « {uname} » ?\n\n"
            "La personne ne pourra plus se connecter tant qu'un administrateur "
            "ne le déverrouille pas ou ne réinitialise son mot de passe.",
            default_no=True,
        ):
            return
        try:
            self._perform_auth_change("lock_user", uname)
        except ValueError as e:
            QMessageBox.warning(self, "Compte", str(e))
            return
        self.reload()
        QMessageBox.information(self, "Compte", f"Le compte « {uname} » est verrouillé.")

    def _revoke_all_user_sessions(self) -> None:
        uname = self._selected_username()
        if not uname:
            QMessageBox.information(self, "Sessions", "Sélectionnez un utilisateur.")
            return
        if not confirm_yes_no(
            self,
            "Déconnecter tous les postes",
            f"Clôturer toutes les sessions actives du compte « {uname} » ?\n\n"
            "Chaque poste concerné affichera l'écran de verrouillage et devra "
            "se reconnecter. Les jetons de session en base seront invalidés.",
            default_no=True,
        ):
            return
        try:
            n = self._perform_auth_change("revoke_all_sessions_for_user", uname)
        except ValueError as e:
            QMessageBox.warning(self, "Sessions", str(e))
            return
        if self._tabs.currentIndex() == 3:
            self._reload_active_sessions()
        QMessageBox.information(
            self,
            "Sessions",
            f"{n} session(s) clôturée(s) pour « {uname} ».\n"
            "Les postes actifs seront verrouillés sous quelques secondes.",
        )
