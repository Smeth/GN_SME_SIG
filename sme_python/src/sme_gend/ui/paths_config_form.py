"""Formulaire paramètres : chemins par catégorie (bases, modèles, archives, médias)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
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
from sme_gend.services.audit_log import append_audit_event
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class PathsConfigForm(QWidget):
    """Paramètres chemins — catégories : bases de données, modèles Excel, archives, médias."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: Settings | None = None,
        focus_database: bool = False,
    ) -> None:
        super().__init__(parent)
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
            sql_init = (
                str(settings.sqlite_liste_path)
                if settings.sqlite_liste_path
                else ""
            )
            use_sql_init = settings.use_sqlite_liste_generale
        else:
            db_init = user.database_path or ""
            tpl_init = user.template_excel_path or ""
            tnum_init = user.template_numerique_path or ""
            tdet_init = user.template_detaches_path or ""
            sql_init = user.sqlite_liste_path or ""
            use_sql_init = user.use_sqlite_liste_generale

        arch_init = user.sme_archives_path or ""
        media_init = user.personnel_media_root_path or ""
        def_arch = user_application_data_dir() / "sme_archives"
        def_media = user_application_data_dir() / "personnel_media"

        self._hint = QLabel(
            "Priorité : variables d’environnement si définies, puis ce formulaire (fichier "
            "<code>paths.json</code>). "
            "<b>SMEDB_PATH</b>, <b>SME_SQLITE_LISTE_PATH</b>, <b>SME_USE_SQLITE_LISTE</b>, "
            "<b>SME_TEMPLATE_PATH</b>, <b>SME_NUMERIQUE_TEMPLATE_PATH</b>, <b>SME_DETACHES_PATH</b>, "
            "<b>SME_ARCHIVES_PATH</b>, <b>SME_PERSONNEL_MEDIA_PATH</b>. "
            "Chemins réseau (UNC) acceptés. "
            "<b>SQLite</b> = données principales en exploitation ; <b>Access</b> = import depuis l’ancien .mdb. "
            "Après enregistrement, l’application recharge la configuration sans redémarrage."
        )
        self._hint.setWordWrap(True)
        self._hint.setTextFormat(Qt.TextFormat.RichText)

        # ——— Bases de données ———
        self._db = QLineEdit(db_init)
        self._db.setPlaceholderText("Fichier .mdb / .accdb")
        btn_db = QPushButton("Parcourir…")
        btn_db.clicked.connect(self._browse_db)
        row_db = QHBoxLayout()
        row_db.addWidget(self._db, stretch=1)
        row_db.addWidget(btn_db)

        self._use_sqlite = QCheckBox(
            "Mode SQLite (recommandé) : liste générale, référentiels, TabSme et tables satellites dans le .sqlite ; "
            "Access sert uniquement aux imports depuis l’ancien système"
        )
        self._use_sqlite.setChecked(use_sql_init)
        self._sql = QLineEdit(sql_init)
        self._sql.setPlaceholderText("Fichier .sqlite du bundle (vide = défaut data/liste_generale.sqlite)")
        btn_sql = QPushButton("Parcourir…")
        btn_sql.clicked.connect(self._browse_sql)
        row_sql = QHBoxLayout()
        row_sql.addWidget(self._sql, stretch=1)
        row_sql.addWidget(btn_sql)

        self._btn_import_sql = QPushButton("Importer Access → SQLite (bundle complet)…")
        self._btn_import_sql.clicked.connect(self._on_import_sqlite)

        form_db = QFormLayout()
        form_db.addRow("Access (.mdb — import / ancien système) :", row_db)
        form_db.addRow(self._use_sqlite)
        form_db.addRow("Fichier SQLite (bundle) :", row_sql)

        grp_db = QGroupBox("Bases de données")
        grp_db.setObjectName("settingsPathsGroup")
        lay_db = QVBoxLayout(grp_db)
        lay_db.addLayout(form_db)
        lay_db.addWidget(self._btn_import_sql)

        # ——— Modèles Excel ———
        self._tpl = QLineEdit(tpl_init)
        self._tpl.setPlaceholderText("Maquette liste des présents (resultat.xlsx, etc.)")
        btn_tpl = QPushButton("Parcourir…")
        btn_tpl.clicked.connect(self._browse_tpl)
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(self._tpl, stretch=1)
        row_tpl.addWidget(btn_tpl)

        self._tnum = QLineEdit(tnum_init)
        self._tnum.setPlaceholderText("Gabarit situation numérique (numerique.xlsx)")
        btn_tnum = QPushButton("Parcourir…")
        btn_tnum.clicked.connect(self._browse_tnum)
        row_tnum = QHBoxLayout()
        row_tnum.addWidget(self._tnum, stretch=1)
        row_tnum.addWidget(btn_tnum)

        self._tdet = QLineEdit(tdet_init)
        self._tdet.setPlaceholderText("Fichier officiel DETACHES.xlsx")
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
            "Copies horodatées à la clôture mensuelle (snapshots SQLite, JSON mise en page). "
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

        self._cfg_path = QLabel(f"Fichier de configuration : <code>{user_paths_config_file()}</code>")
        self._cfg_path.setWordWrap(True)
        self._cfg_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(self._hint)
        lay.addWidget(grp_db)
        lay.addWidget(grp_tpl)
        lay.addWidget(grp_arch)
        lay.addWidget(grp_media)
        lay.addWidget(self._cfg_path)

        if focus_database:
            self._db.setFocus()

    def try_save(self, *, confirm: bool) -> bool:
        db_raw = self._db.text().strip()
        tpl_raw = self._tpl.text().strip()
        tnum_raw = self._tnum.text().strip()
        tdet_raw = self._tdet.text().strip()
        sql_raw = self._sql.text().strip()
        arch_raw = self._arch.text().strip()
        media_raw = self._media.text().strip()

        if db_raw:
            p = Path(db_raw)
            if not p.is_file() and not self._use_sqlite.isChecked():
                QMessageBox.warning(
                    self,
                    "Base Access",
                    f"Le fichier base n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if tpl_raw:
            p = Path(tpl_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Maquette Excel",
                    f"Le fichier maquette n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if tnum_raw:
            p = Path(tnum_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Gabarit numérique",
                    f"Le fichier gabarit n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if tdet_raw:
            p = Path(tdet_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Fichier détachés",
                    f"Le fichier DETACHES.xlsx n’existe pas ou n’est pas accessible :\n{p}",
                )
                return False

        if arch_raw:
            p = Path(arch_raw).expanduser()
            if p.is_file():
                QMessageBox.warning(
                    self,
                    "Archives",
                    "Indiquez un dossier pour les archives, pas un fichier.",
                )
                return False

        if media_raw:
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

        prev = load_user_paths()
        save_user_paths(
            database_path=db_raw if db_raw else None,
            template_excel_path=tpl_raw if tpl_raw else None,
            template_numerique_path=tnum_raw if tnum_raw else None,
            template_detaches_path=tdet_raw if tdet_raw else None,
            sqlite_liste_path=sql_raw if sql_raw else None,
            use_sqlite_liste_generale=self._use_sqlite.isChecked(),
            sme_archives_path=arch_raw if arch_raw else None,
            personnel_media_root_path=media_raw if media_raw else None,
        )
        new_use = self._use_sqlite.isChecked()
        new_sql = sql_raw if sql_raw else None
        if (
            prev.use_sqlite_liste_generale != new_use
            or (prev.sqlite_liste_path or "").strip() != (new_sql or "").strip()
        ):
            append_audit_event(
                action="user_paths_sqlite_settings_saved",
                use_sqlite_previous=prev.use_sqlite_liste_generale,
                use_sqlite_saved=new_use,
                sqlite_path_previous=prev.sqlite_liste_path,
                sqlite_path_saved=new_sql,
            )
        return True

    def refresh_from_disk(self, settings: Settings) -> None:
        """Met à jour les champs après un `get_settings()` (chemins déjà enregistrés)."""
        user = load_user_paths()
        self._db.setText(str(settings.database_path))
        self._use_sqlite.setChecked(settings.use_sqlite_liste_generale)
        self._sql.setText(str(settings.sqlite_liste_path) if settings.sqlite_liste_path else "")
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
        self._media.setText(user.personnel_media_root_path or "")

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

    def _browse_sql(self) -> None:
        start = self._sql.text().strip() or str(project_root() / "data")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Fichier SQLite liste générale",
            start,
            "SQLite (*.sqlite *.db);;Tous les fichiers (*)",
        )
        if path:
            self._sql.setText(path)

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

    def _on_import_sqlite(self) -> None:
        db_raw = self._db.text().strip()
        if not db_raw or not Path(db_raw).is_file():
            QMessageBox.warning(
                self,
                "Import",
                "Indiquez une base Access valide contenant la table ListeGenerale.",
            )
            return
        sql_raw = self._sql.text().strip()
        sql_p = (
            Path(sql_raw).expanduser()
            if sql_raw
            else (project_root() / "data" / "liste_generale.sqlite")
        )
        sql_p = sql_p.resolve()
        sql_p.parent.mkdir(parents=True, exist_ok=True)
        if not confirm_yes_no(
            self,
            "Importer vers SQLite",
            f"Copier depuis Access vers :\n{sql_p}\n\n"
            "• Référentiels (structures, grades, subdivisions), TabSme, tables mission/malade/…\n"
            "• Liste générale (upsert par id_acces)\n\n"
            "Le contenu des tables SQLite concernées est remplacé (sauf clés internes liste).",
            default_no=True,
        ):
            return
        try:
            access_st = Settings(
                database_path=Path(db_raw).resolve(),
                sqlite_liste_path=sql_p,
                use_sqlite_liste_generale=False,
            )
            from sme_gend.services.sqlite_bundle_import import (
                import_full_sqlite_bundle_from_access,
            )

            result = import_full_sqlite_bundle_from_access(access_st, sql_p)
            ins = result["liste_inserted"]
            upd = result["liste_updated"]
            warns = result["referentials"].get("warnings") or []
            msg = f"Liste générale — insertions : {ins}, mises à jour : {upd}."
            if warns:
                msg += "\n\nAvertissements (tables Access absentes ou erreurs) :\n• " + "\n• ".join(
                    warns
                )
            QMessageBox.information(self, "Import terminé", msg)
        except OSError as e:
            append_audit_event(action="sqlite_bundle_import_failed", error=str(e))
            QMessageBox.critical(self, "Import", str(e))
        except Exception as e:
            append_audit_event(action="sqlite_bundle_import_failed", error=str(e))
            QMessageBox.critical(self, "Import", str(e))
