"""Formulaire paramètres : chemins par catégorie (bases, modèles, archives, médias)."""

from __future__ import annotations

from sme_gend.config.ui_labels import IMPORT_MONTH_ACCESS_ACTION, IMPORT_MONTH_MENU_PATH

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings, project_root
from sme_gend.config.user_paths import (
    load_user_paths,
    save_user_paths,
    user_application_data_dir,
    user_paths_config_file,
)
from sme_gend.ui.confirm_dialogs import confirm_yes_no


def _assert_configuration_access(*, bootstrap_configuration: bool = False) -> bool:
    """Connection repair before auth cannot grant runtime administration rights."""
    if bootstrap_configuration:
        from sme_gend.services.auth_context import get_app_auth
        if get_app_auth() is not None:
            raise PermissionError("La configuration initiale est terminée. Connectez-vous avec les droits de paramétrage.")
        return True
    from sme_gend.services.action_permissions import assert_any_permission
    assert_any_permission("system.settings")
    return False


class PathsConfigForm(QWidget):
    """Paramètres chemins — catégories : bases de données, modèles Excel, archives, médias."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: Settings | None = None,
        focus_database: bool = False,
        bootstrap_configuration: bool = False,
    ) -> None:
        super().__init__(parent)
        self._bootstrap_configuration = _assert_configuration_access(
            bootstrap_configuration=bootstrap_configuration)
        user = load_user_paths()
        if settings is not None:
            db_init = str(settings.database_path)
            tpl_init = (
                str(settings.template_liste_present_path)
                if settings.template_liste_present_path
                else ""
            )
            tnum_init = (
                str(settings.template_numerique_path)
                if settings.template_numerique_path
                else ""
            )
            tdet_init = (
                str(settings.template_detaches_path)
                if settings.template_detaches_path
                else ""
            )
            mysql_host_init = settings.mysql_host
            mysql_port_init = str(settings.mysql_port)
            mysql_db_init = settings.mysql_database
            mysql_user_init = settings.mysql_user
            mysql_pwd_init = settings.mysql_password
        else:
            db_init = user.database_path or ""
            tpl_init = user.template_excel_path or ""
            tnum_init = user.template_numerique_path or ""
            tdet_init = user.template_detaches_path or ""
            mysql_host_init = user.mysql_host or "127.0.0.1"
            mysql_port_init = str(user.mysql_port or 3306)
            mysql_db_init = user.mysql_database or "smegend"
            mysql_user_init = user.mysql_user or "root"
            mysql_pwd_init = user.mysql_password or ""

        arch_init = user.sme_archives_path or ""
        media_init = user.personnel_media_root_path or ""
        from sme_gend.services.shared_media import shared_media_root
        shared_root = None if self._bootstrap_configuration else shared_media_root()
        if shared_root is not None and not user.personnel_media_local_override:
            media_init = str(shared_root)
        def_arch = user_application_data_dir() / "sme_archives"
        def_media = user_application_data_dir() / "personnel_media"

        self._mysql_source = QLabel()
        self._mysql_source.setObjectName("settingsPathsHint")
        self._mysql_source.setWordWrap(True)
        self._mysql_source.setTextFormat(Qt.TextFormat.RichText)
        self._refresh_mysql_source_label()

        self._hint = QLabel(
            "Connexion <b>MySQL</b> obligatoire pour l'exploitation (effectifs, SME, exports). "
            "Après <b>Enregistrer</b>, la configuration est écrite dans <code>paths.json</code> "
            "et prioritaire sur <code>SME_MYSQL_*</code>. "
            "Utilisez <b>Tester la connexion</b> avant d'enregistrer sur un nouveau poste. "
            "<b>Access</b> sert uniquement à l'import depuis l'ancien système."
        )
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)
        self._hint.setObjectName("settingsPathsHint")

        # ——— Bases de données ———
        self._db = QLineEdit(db_init)
        self._db.setPlaceholderText("Fichier .mdb / .accdb")
        btn_db = QPushButton("Parcourir…")
        btn_db.clicked.connect(self._browse_db)
        row_db = QHBoxLayout()
        row_db.addWidget(self._db, stretch=1)
        row_db.addWidget(btn_db)

        self._mysql_host = QLineEdit(mysql_host_init)
        self._mysql_port = QLineEdit(mysql_port_init)
        self._mysql_port.setValidator(QIntValidator(1, 65535, self._mysql_port))
        self._mysql_port.setMaximumWidth(80)
        self._mysql_database = QLineEdit(mysql_db_init)
        self._mysql_user = QLineEdit(mysql_user_init)
        self._mysql_password = QLineEdit(mysql_pwd_init)
        self._mysql_password.setEchoMode(QLineEdit.EchoMode.Password)

        self._btn_test_mysql = QPushButton("Tester la connexion")
        self._btn_test_mysql.clicked.connect(self._on_test_mysql)

        self._btn_test_access = QPushButton("Tester ODBC Access…")
        self._btn_test_access.setToolTip(
            "Vérifie le pilote ODBC et l'ouverture du fichier .mdb/.accdb ci-dessus "
            "(requis pour importer les données mensuelles, pas pour l'archivage seul)."
        )
        self._btn_test_access.clicked.connect(self._on_test_access_odbc)

        self._btn_import_mysql = QPushButton(IMPORT_MONTH_ACCESS_ACTION)
        self._btn_import_mysql.setObjectName("primaryButton")
        self._btn_import_mysql.setToolTip(
            "Ouvre l'assistant d'import Access → MySQL (ODBC Access 64 bits requis) "
            "avec le fichier et la connexion MySQL de cet onglet préremplis. "
            f"Identique à {IMPORT_MONTH_MENU_PATH}"
        )
        self._btn_import_mysql.clicked.connect(self._on_import_mysql)

        self._import_hint = QLabel(
            f"Raccourci vers le même assistant que {IMPORT_MONTH_MENU_PATH}, "
            "avec le fichier et la connexion ci-dessus préremplis."
        )
        self._import_hint.setWordWrap(True)
        self._import_hint.setTextFormat(Qt.TextFormat.RichText)
        self._import_hint.setObjectName("dashSubtitle")

        mysql_btn_row = QHBoxLayout()
        mysql_btn_row.addWidget(self._btn_test_mysql)
        mysql_btn_row.addWidget(self._btn_test_access)
        mysql_btn_row.addWidget(self._btn_import_mysql)
        mysql_btn_row.addStretch()

        form_db = QFormLayout()
        form_db.addRow("Hôte MySQL :", self._mysql_host)
        form_db.addRow("Port :", self._mysql_port)
        form_db.addRow("Base de données :", self._mysql_database)
        form_db.addRow("Utilisateur :", self._mysql_user)
        form_db.addRow("Mot de passe :", self._mysql_password)

        grp_db = QGroupBox("Connexion MySQL")
        grp_db.setObjectName("settingsPathsGroup")
        lay_db = QVBoxLayout(grp_db)
        lay_db.addWidget(self._mysql_source)
        lay_db.addLayout(form_db)
        lay_db.addLayout(mysql_btn_row)
        lay_db.addWidget(self._import_hint)
        lay_db.addWidget(QLabel("Fichier Access (import uniquement) :"))
        lay_db.addLayout(row_db)

        # ——— Modèles Excel ———
        self._tpl = QLineEdit(tpl_init)
        self._tpl.setPlaceholderText("Maquette liste des présents (modeles/resultat.xlsx)")
        btn_tpl = QPushButton("Parcourir…")
        btn_tpl.clicked.connect(self._browse_tpl)
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(self._tpl, stretch=1)
        row_tpl.addWidget(btn_tpl)

        self._tnum = QLineEdit(tnum_init)
        self._tnum.setPlaceholderText("Gabarit situation numérique (modeles/numerique.xlsx)")
        btn_tnum = QPushButton("Parcourir…")
        btn_tnum.clicked.connect(self._browse_tnum)
        row_tnum = QHBoxLayout()
        row_tnum.addWidget(self._tnum, stretch=1)
        row_tnum.addWidget(btn_tnum)

        self._tdet = QLineEdit(tdet_init)
        self._tdet.setPlaceholderText("Fichier officiel (modeles/DETACHES.xlsx)")
        btn_tdet = QPushButton("Parcourir…")
        btn_tdet.clicked.connect(self._browse_tdet)
        row_tdet = QHBoxLayout()
        row_tdet.addWidget(self._tdet, stretch=1)
        row_tdet.addWidget(btn_tdet)

        form_tpl = QFormLayout()
        form_tpl.addRow("Liste des présents :", row_tpl)
        form_tpl.addRow("Situation numérique :", row_tnum)
        form_tpl.addRow("Détachés :", row_tdet)

        grp_tpl = QGroupBox("Modèles et gabarits Excel")
        grp_tpl.setObjectName("settingsPathsGroup")
        lay_tpl = QVBoxLayout(grp_tpl)
        lay_tpl.addLayout(form_tpl)

        # ——— Archives clôture ———
        self._arch = QLineEdit(arch_init)
        self._arch.setPlaceholderText(f"Défaut si vide : {def_arch}")
        btn_arch = QPushButton("Choisir dossier…")
        btn_arch.clicked.connect(self._browse_arch_dir)
        row_arch = QHBoxLayout()
        row_arch.addWidget(self._arch, stretch=1)
        row_arch.addWidget(btn_arch)
        lbl_arch = QLabel(
            "Copies horodatées à la clôture (archives .mdb Access + snapshot layout MySQL). "
            "Laisser vide pour utiliser le dossier par défaut sous le profil utilisateur."
        )
        lbl_arch.setWordWrap(True)
        lbl_arch.setObjectName("settingsPathsHint")
        form_arch = QFormLayout()
        form_arch.addRow("Dossier des archives SME :", row_arch)
        grp_arch = QGroupBox("Archives de clôture")
        grp_arch.setObjectName("settingsPathsGroup")
        lay_arch = QVBoxLayout(grp_arch)
        lay_arch.addWidget(lbl_arch)
        lay_arch.addLayout(form_arch)

        # ——— Médias fiches ———
        self._media = QLineEdit(media_init)
        self._media.setPlaceholderText(f"Défaut si vide : {def_media}")
        btn_media = QPushButton("Choisir dossier…")
        btn_media.clicked.connect(self._browse_media_dir)
        row_media = QHBoxLayout()
        row_media.addWidget(self._media, stretch=1)
        row_media.addWidget(btn_media)
        lbl_media = QLabel(
            "Racine pour les photos d’identité et insignes de grade (fiche dossier). "
            "Les lecteurs réseau (Z:, P:…) sont convertis en chemin UNC commun à tous les postes. "
            "Les anciennes photos locales sont copiées sans écraser celles du partage. "
            "Sous-dossiers attendus : <code>photos</code> (fichiers <code>{{id}}.jpg</code>) et "
            "<code>grades</code> (images par grade). Créés automatiquement si absents."
        )
        lbl_media.setWordWrap(True)
        lbl_media.setObjectName("settingsPathsHint")
        form_media = QFormLayout()
        form_media.addRow("Dossier racine médias personnel :", row_media)
        grp_media = QGroupBox("Stockage des images (fiches personnel)")
        grp_media.setObjectName("settingsPathsGroup")
        lay_media = QVBoxLayout(grp_media)
        lay_media.addWidget(lbl_media)
        lay_media.addLayout(form_media)

        # ——— Application (Windows) ———
        from sme_gend.services.windows_startup import is_startup_enabled, is_supported

        from sme_gend.config.branding import APP_NAME

        self._chk_startup = QCheckBox(f"Lancer {APP_NAME} au démarrage de Windows")
        self._chk_startup.setToolTip(
            "Ajoute ou retire une entrée dans le registre Windows "
            "(HKCU\\…\\Run) pour ouvrir l'application à la connexion."
        )
        self._chk_startup.setEnabled(is_supported())
        self._chk_startup.setChecked(is_startup_enabled() if is_supported() else False)

        lbl_startup = QLabel(
            "Cette option s'applique au poste courant uniquement. "
            "Sous Windows, elle utilise le chemin de l'exécutable portable "
            "ou, en développement, <code>python -m sme_gend</code>."
        )
        lbl_startup.setWordWrap(True)
        lbl_startup.setTextFormat(Qt.TextFormat.RichText)
        lbl_startup.setObjectName("settingsPathsHint")

        lbl_notif = QLabel(
            "<b>Notifications Windows</b> — l'application peut afficher des alertes via "
            "la zone de notification (icône dans la barre des tâches) ou, avec un module "
            "complémentaire (<code>winrt</code>), des toasts natifs du Centre de notifications. "
            "Aucune notification système n'est encore activée par défaut."
        )
        lbl_notif.setWordWrap(True)
        lbl_notif.setTextFormat(Qt.TextFormat.RichText)
        lbl_notif.setObjectName("settingsPathsHint")

        grp_app = QGroupBox("Application")
        grp_app.setObjectName("settingsPathsGroup")
        lay_app = QVBoxLayout(grp_app)
        lay_app.addWidget(self._chk_startup)
        lay_app.addWidget(lbl_startup)
        lay_app.addWidget(lbl_notif)

        self._cfg_path = QLabel(f"Fichier de configuration : <code>{user_paths_config_file()}</code>")
        self._cfg_path.setWordWrap(True)
        self._cfg_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        tabs = QTabWidget()
        tabs.setObjectName("settingsTabs")
        tab_mysql = QWidget()
        tab_mysql_lay = QVBoxLayout(tab_mysql)
        tab_mysql_lay.setContentsMargins(8, 12, 8, 8)
        tab_mysql_lay.addWidget(grp_db)
        tabs.addTab(tab_mysql, "MySQL & import")

        tab_tpl = QWidget()
        tab_tpl_lay = QVBoxLayout(tab_tpl)
        tab_tpl_lay.setContentsMargins(8, 12, 8, 8)
        tab_tpl_lay.addWidget(grp_tpl)
        tabs.addTab(tab_tpl, "Modèles Excel")

        tab_files = QWidget()
        tab_files_lay = QVBoxLayout(tab_files)
        tab_files_lay.setContentsMargins(8, 12, 8, 8)
        tab_files_lay.addWidget(grp_arch)
        tab_files_lay.addWidget(grp_media)
        tabs.addTab(tab_files, "Archives & médias")

        tab_app = QWidget()
        tab_app_lay = QVBoxLayout(tab_app)
        tab_app_lay.setContentsMargins(8, 12, 8, 8)
        tab_app_lay.addWidget(grp_app)
        tab_app_lay.addStretch()
        tabs.addTab(tab_app, "Application")

        if self._bootstrap_configuration:
            # Before authentication only the local connection can be repaired.
            # Imports and shared storage switches require an authenticated role.
            for index in range(1, tabs.count()):
                tabs.setTabEnabled(index, False)
            self._btn_import_mysql.setEnabled(False)
            self._btn_test_access.setEnabled(False)
            self._db.setEnabled(False)
            btn_db.setEnabled(False)
            self._chk_startup.setEnabled(False)
            self._import_hint.setText("Connectez-vous pour importer les archives et configurer les autres paramètres.")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        from sme_gend.ui.expandable_help import ExpandableHelp
        self._help = ExpandableHelp(self._hint.text(), self)
        self._hint.hide()
        tabs.setCornerWidget(self._help.button, Qt.Corner.TopRightCorner)
        lay.addWidget(self._help)
        lay.addWidget(tabs, stretch=1)
        lay.addWidget(self._cfg_path)

        if focus_database:
            tabs.setCurrentIndex(0)
            self._mysql_host.setFocus()

    def try_save(self, *, confirm: bool) -> bool:
        bootstrap = getattr(self, "_bootstrap_configuration", False) is True
        try:
            _assert_configuration_access(bootstrap_configuration=bootstrap)
        except PermissionError as exc:
            QMessageBox.warning(self, "Droits", str(exc))
            return False
        db_raw = self._db.text().strip()
        tpl_raw = self._tpl.text().strip()
        tnum_raw = self._tnum.text().strip()
        tdet_raw = self._tdet.text().strip()
        arch_raw = self._arch.text().strip()
        media_raw = self._media.text().strip()
        previous_paths = load_user_paths() if bootstrap else None
        if bootstrap:
            # Preserve all other local preferences, even paths unavailable on
            # this workstation; repairing MySQL must not replace those fields.
            db_raw = previous_paths.database_path or ""
            tpl_raw = previous_paths.template_excel_path or ""
            tnum_raw = previous_paths.template_numerique_path or ""
            tdet_raw = previous_paths.template_detaches_path or ""
            arch_raw = previous_paths.sme_archives_path or ""
            media_raw = previous_paths.personnel_media_root_path or ""
        mysql_host = self._mysql_host.text().strip() or "127.0.0.1"
        mysql_db = self._mysql_database.text().strip() or "smegend"
        try:
            mysql_port = self._validated_mysql_port()
        except ValueError:
            QMessageBox.warning(self, "MySQL", "Le port doit être un nombre entier entre 1 et 65535.")
            self._mysql_port.setFocus()
            return False

        if db_raw and not bootstrap:
            p = Path(db_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Base Access",
                    f"Le fichier Access n’existe pas (requis pour l’import) :\n{p}",
                )
                return False

        if tpl_raw and not bootstrap:
            p = Path(tpl_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Maquette Excel",
                    f"Le fichier maquette n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if tnum_raw and not bootstrap:
            p = Path(tnum_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Gabarit numérique",
                    f"Le fichier gabarit n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if tdet_raw and not bootstrap:
            p = Path(tdet_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Fichier détachés",
                    f"Le fichier DETACHES.xlsx n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if arch_raw and not bootstrap:
            p = Path(arch_raw).expanduser()
            if p.is_file():
                QMessageBox.warning(
                    self,
                    "Archives",
                    "Indiquez un dossier pour les archives, pas un fichier.",
                )
                return False

        if media_raw and not bootstrap:
            p = Path(media_raw).expanduser()
            if p.is_file():
                QMessageBox.warning(
                    self,
                    "Médias",
                    "Indiquez un dossier racine pour les médias, pas un fichier.",
                )
                return False

        if confirm:
            if not confirm_yes_no(
                self,
                "Enregistrer les paramètres",
                "Enregistrer ces chemins dans le fichier de configuration utilisateur ?\n\n"
                "La configuration sera rechargée immédiatement après validation (pas de redémarrage nécessaire).",
                default_no=True,
            ):
                return False

        mysql_st = self._build_mysql_settings()
        from sme_gend.repositories.mysql_connection import MySQLConnection

        ok_mysql, msg_mysql = MySQLConnection(mysql_st).test_connection()
        if not ok_mysql:
            from sme_gend.services.mysql_health import format_mysql_error_for_user

            friendly = format_mysql_error_for_user(msg_mysql)
            if not confirm_yes_no(
                self,
                "MySQL injoignable",
                f"{friendly}\n\nEnregistrer quand même ces paramètres ?\n"
                "(Utile si le serveur n'est pas encore accessible depuis ce poste.)",
                default_no=True,
            ):
                return False

        # Rights can change while a dialog is open or the connection is tested.
        try:
            _assert_configuration_access(bootstrap_configuration=bootstrap)
        except PermissionError as exc:
            QMessageBox.warning(self, "Droits", str(exc))
            return False
        if media_raw and not bootstrap:
            from sme_gend.services.personnel_media import personnel_media_root
            from sme_gend.services.shared_media import configure_shared_media
            try:
                from sme_gend.ui.bg_task import run_with_progress
                selected_media = media_raw
                previous_media = personnel_media_root()
                media_raw = run_with_progress(
                    self, "Médias partagés", "Vérification des autres postes et copie des images…",
                    lambda: configure_shared_media(mysql_st, selected_media, previous_media),
                )
                if media_raw is None:
                    return False
            except Exception as exc:
                QMessageBox.warning(self, "Médias partagés", f"Configuration impossible :\n{exc}")
                return False
            self._media.setText(media_raw)

        try:
            _assert_configuration_access(bootstrap_configuration=bootstrap)
        except PermissionError as exc:
            QMessageBox.warning(self, "Droits", str(exc))
            return False
        save_user_paths(
            database_path=db_raw if db_raw else None,
            template_excel_path=tpl_raw if tpl_raw else None,
            template_numerique_path=tnum_raw if tnum_raw else None,
            template_detaches_path=tdet_raw if tdet_raw else None,
            sme_archives_path=arch_raw if arch_raw else None,
            personnel_media_root_path=media_raw if media_raw else None,
            personnel_media_local_override=(previous_paths.personnel_media_local_override if bootstrap
                                            else bool(media_raw and not media_raw.startswith(("\\\\", "//")))),
            mysql_host=mysql_host,
            mysql_port=mysql_port,
            mysql_database=mysql_db,
            mysql_user=self._mysql_user.text().strip() or "root",
            mysql_password=self._mysql_password.text(),
        )
        # This local save is already committed. A blocked/unavailable database
        # must not turn it into a fatal error or invite a duplicate save.
        from sme_gend.services.connection_config_audit import record_connection_config_saved
        audit_warning = record_connection_config_saved(mysql_st)
        if audit_warning:
            QMessageBox.warning(self, "Paramètres enregistrés", audit_warning)
        if not bootstrap and self._chk_startup.isEnabled():
            from sme_gend.services.windows_startup import apply_startup_preference

            try:
                apply_startup_preference(self._chk_startup.isChecked())
            except OSError as exc:
                QMessageBox.warning(
                    self,
                    "Démarrage Windows",
                    f"Impossible de modifier le lancement au démarrage :\n{exc}",
                )
                return False
        return True

    def _refresh_mysql_source_label(self) -> None:
        from sme_gend.config.settings import mysql_config_source_label

        src = mysql_config_source_label()
        self._mysql_source.setText(
            f"<b>Source active pour l'application :</b> {src}"
        )

    def refresh_from_disk(self, settings: Settings) -> None:
        """Met à jour les champs après un `get_settings()` (chemins déjà enregistrés)."""
        _assert_configuration_access(bootstrap_configuration=self._bootstrap_configuration)
        self._refresh_mysql_source_label()
        user = load_user_paths()
        self._db.setText(str(settings.database_path))
        self._mysql_host.setText(settings.mysql_host)
        self._mysql_port.setText(str(settings.mysql_port))
        self._mysql_database.setText(settings.mysql_database)
        self._mysql_user.setText(settings.mysql_user)
        self._mysql_password.setText(settings.mysql_password)
        self._tpl.setText(
            str(settings.template_liste_present_path) if settings.template_liste_present_path else ""
        )
        self._tnum.setText(
            str(settings.template_numerique_path) if settings.template_numerique_path else ""
        )
        self._tdet.setText(
            str(settings.template_detaches_path) if settings.template_detaches_path else ""
        )
        self._arch.setText(user.sme_archives_path or "")
        from sme_gend.services.shared_media import shared_media_root
        shared_root = shared_media_root()
        self._media.setText(str(shared_root) if shared_root and not user.personnel_media_local_override else user.personnel_media_root_path or "")
        if self._chk_startup.isEnabled():
            from sme_gend.services.windows_startup import is_startup_enabled

            self._chk_startup.setChecked(is_startup_enabled())

    def _browse_db(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Fichier base Access",
            self._db.text() or str(Path.home()),
            "Access (*.mdb *.accdb);;Tous les fichiers (*)",
        )
        if path:
            self._db.setText(path)

    def _browse_tpl(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Maquette Excel",
            self._tpl.text() or str(Path.home()),
            "Excel (*.xlsx *.xlsm);;Tous les fichiers (*)",
        )
        if path:
            self._tpl.setText(path)

    def _browse_tnum(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Gabarit situation numérique",
            self._tnum.text() or str(Path.home()),
            "Excel (*.xlsx *.xlsm);;Tous les fichiers (*)",
        )
        if path:
            self._tnum.setText(path)

    def _browse_tdet(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Fichier DETACHES.xlsx",
            self._tdet.text() or str(Path.home()),
            "Excel (*.xlsx *.xlsm);;Tous les fichiers (*)",
        )
        if path:
            self._tdet.setText(path)

    def _browse_arch_dir(self) -> None:
        start = self._arch.text().strip() or str(user_application_data_dir())
        d = QFileDialog.getExistingDirectory(
            self,
            "Dossier des archives de clôture SME",
            start,
        )
        if d:
            self._arch.setText(d)

    def _browse_media_dir(self) -> None:
        start = self._media.text().strip() or str(user_application_data_dir())
        d = QFileDialog.getExistingDirectory(
            self,
            "Dossier racine — médias fiches personnel (photos / grades)",
            start,
        )
        if d:
            self._media.setText(d)

    def _validated_mysql_port(self) -> int:
        port = int(self._mysql_port.text().strip() or "3306")
        if not 1 <= port <= 65535:
            raise ValueError("Le port doit être compris entre 1 et 65535.")
        return port

    def _build_mysql_settings(self, *, from_form: bool = True) -> Settings:
        """Paramètres MySQL : champs du formulaire (test avant enregistrement) ou disque."""
        from sme_gend.config import get_settings

        port = self._validated_mysql_port() if from_form else None
        base = get_settings()
        db_raw = self._db.text().strip()
        if from_form:
            return Settings(
                database_path=Path(db_raw).resolve() if db_raw else base.database_path,
                template_liste_present_path=base.template_liste_present_path,
                template_numerique_path=base.template_numerique_path,
                template_detaches_path=base.template_detaches_path,
                mysql_host=self._mysql_host.text().strip() or "127.0.0.1",
                mysql_port=port,
                mysql_database=self._mysql_database.text().strip() or "smegend",
                mysql_user=self._mysql_user.text().strip() or "root",
                mysql_password=self._mysql_password.text(),
            )
        return base

    def _on_test_mysql(self) -> None:
        from sme_gend.repositories.mysql_connection import MySQLConnection
        from sme_gend.ui.bg_task import run_with_progress

        try:
            st = self._build_mysql_settings()
        except ValueError:
            QMessageBox.warning(self, "MySQL", "Le port doit être un nombre entier entre 1 et 65535.")
            self._mysql_port.setFocus()
            return

        def work():
            return MySQLConnection(st).test_connection()

        self._btn_test_mysql.setEnabled(False)
        try:
            result = run_with_progress(
                self,
                "MySQL",
                "Test de connexion en cours…",
                work,
                silent=True,
            )
        finally:
            self._btn_test_mysql.setEnabled(True)
        if result is None:
            return
        ok, msg = result
        if ok:
            QMessageBox.information(self, "MySQL", msg)
        else:
            QMessageBox.warning(self, "MySQL", msg)

    def _on_test_access_odbc(self) -> None:
        db_raw = self._db.text().strip()
        if not db_raw or not Path(db_raw).is_file():
            QMessageBox.warning(
                self,
                "ODBC Access",
                "Indiquez d'abord un fichier .mdb ou .accdb valide.",
            )
            return
        from sme_gend.services.access_odbc import preflight_access_file

        ok, msg = preflight_access_file(db_raw)
        if ok:
            QMessageBox.information(self, "ODBC Access", f"Connexion OK.\n\n{msg}")
        else:
            QMessageBox.critical(self, "ODBC Access", msg)

    def _on_import_mysql(self) -> None:
        from sme_gend.services.auth_guard import require_permission
        if (getattr(self, "_bootstrap_configuration", False) is True
                or not require_permission(self, "workflow.import_archive")):
            return
        db_raw = self._db.text().strip()
        if not db_raw or not Path(db_raw).is_file():
            QMessageBox.warning(
                self,
                "Import",
                "Indiquez une base Access valide (.mdb / .accdb) contenant ListeGenerale.",
            )
            return
        from sme_gend.ui.import_liste_generale_wizard import (
            run_import_liste_generale_from_access,
        )

        run_import_liste_generale_from_access(
            self, self._build_mysql_settings(), initial_mdb=Path(db_raw)
        )
