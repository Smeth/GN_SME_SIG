"""Sauvegarde et restauration complète ou partielle de l'application."""

from __future__ import annotations

import zipfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.settings import Settings
from sme_gend.services.app_health_service import (
    backup_storage_dir,
    check_mysql_connectivity,
    find_latest_backup_zip,
    list_backup_archives,
)
from sme_gend.services.backup_restore_service import (
    archive_contents_summary,
    create_backup,
    create_backup_to_default_dir,
    create_full_backup,
    default_backup_filename,
    describe_backup_scope,
    format_audit_journal_summary,
    inspect_backup,
    list_mysql_table_names,
    prune_old_backups,
    restore_backup,
    restore_full_backup,
)
from sme_gend.services.workflow_permissions import session_can_perform_action
from sme_gend.ui.backup_scope_widgets import scope_group_box
from sme_gend.ui.bg_task import run_in_background, run_with_progress
from sme_gend.ui.page_header import PageHeader


class BackupRestoreView(QWidget):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._last_backup: Path | None = None
        self._partial_restore_zip: Path | None = None
        self._health_cache: tuple[float, bool, str, Path | None] | None = None
        self._health_ttl_sec = 90.0

        header = PageHeader(
            "Sauvegarde & restauration",
            "Archives ZIP portables : sauvegarde <b>complète</b> (tout) ou "
            "<b>partielle</b> (MySQL par tables, médias photos/insignes seuls, "
            "config, data\\). Restauration complète ou partielle depuis la même archive. "
            "Les comptes partagés de cette base peuvent être conservés à la restauration.",
        )

        self._health_lbl = QLabel("Vérification MySQL…")
        self._health_lbl.setWordWrap(True)
        self._health_lbl.setObjectName("settingsPathsHint")

        g_backup = QGroupBox("Sauvegarde complète")
        bl = QVBoxLayout(g_backup)
        self._btn_backup_quick = QPushButton("Sauvegarde rapide (dossier SME)")
        self._btn_backup_quick.setObjectName("primaryButton")
        self._btn_backup_quick.setToolTip(str(backup_storage_dir()))
        self._btn_backup_quick.clicked.connect(self._on_backup_quick)
        self._btn_backup = QPushButton("Sauvegarde complète (choisir l'emplacement)…")
        self._btn_backup.clicked.connect(self._on_backup)
        bl.addWidget(self._btn_backup_quick)
        bl.addWidget(self._btn_backup)
        self._lbl_backup_dir = QLabel()
        self._lbl_backup_dir.setWordWrap(True)
        self._lbl_backup_dir.setObjectName("settingsPathsHint")
        bl.addWidget(self._lbl_backup_dir)

        g_partial_b, self._backup_scope = scope_group_box(
            "Sauvegarde partielle — composants à inclure"
        )
        pb_row = QHBoxLayout()
        self._btn_partial_quick = QPushButton("Sauvegarde partielle (dossier SME)")
        self._btn_partial_quick.clicked.connect(
            lambda: self._on_partial_backup(to_default_dir=True)
        )
        self._btn_partial = QPushButton("Sauvegarde partielle (choisir l’emplacement)…")
        self._btn_partial.clicked.connect(
            lambda: self._on_partial_backup(to_default_dir=False)
        )
        pb_row.addWidget(self._btn_partial_quick)
        pb_row.addWidget(self._btn_partial)
        g_partial_b.layout().addLayout(pb_row)

        g_restore = QGroupBox("Restauration complète")
        rl = QVBoxLayout(g_restore)
        self._chk_config = QCheckBox(
            "Restaurer préférences locales (contexte SME, thème, colonnes — sans connexion MySQL)"
        )
        self._chk_config.setChecked(True)
        self._chk_mysql = QCheckBox(
            "Conserver la connexion MySQL de ce poste (hôte, base, identifiant technique, mot de passe)"
        )
        self._chk_mysql.setChecked(True)
        self._chk_keep_accounts = QCheckBox(
            "Conserver les comptes partagés de cette base (utilisateurs, rôles et mots de passe)"
        )
        self._chk_keep_accounts.setChecked(True)
        self._chk_keep_accounts.setToolTip(
            "La base restaurée ne remplace pas les comptes déjà créés dans cette base."
        )
        self._chk_media = QCheckBox("Restaurer photos et insignes (personnel_media)")
        self._chk_media.setChecked(True)
        self._chk_preserve_audit = QCheckBox(
            "Conserver le journal d'audit de ce poste (MySQL + miroirs locaux)"
        )
        self._chk_preserve_audit.setChecked(True)
        self._chk_preserve_audit.setText("Conserver le journal courant de cette base (tous les postes) et les miroirs locaux")
        self._chk_data = QCheckBox(
            "Restaurer les fichiers data\\ (configs JSON, journaux locaux non sensibles)"
        )
        self._chk_data.setChecked(False)
        self._chk_publications = QCheckBox("Restaurer les versions archivées des classeurs SME")
        self._chk_publications.setChecked(True)
        self._lbl_warn = QLabel(
            "⚠ La restauration complète <b>remplace</b> toute la base MySQL de l'archive "
            "(sauf audit si option cochée). Fermez les autres sessions SME avant de continuer."
        )
        self._lbl_warn.setWordWrap(True)
        self._lbl_warn.setTextFormat(Qt.TextFormat.RichText)
        self._btn_restore = QPushButton("Restaurer une sauvegarde complète de l’application…")
        self._btn_restore.setObjectName("primaryButton")
        self._btn_restore.clicked.connect(self._on_restore_full)
        rl.addWidget(self._lbl_warn)
        rl.addWidget(self._chk_config)
        rl.addWidget(self._chk_mysql)
        rl.addWidget(self._chk_keep_accounts)
        rl.addWidget(self._chk_media)
        rl.addWidget(self._chk_preserve_audit)
        rl.addWidget(self._chk_data)
        rl.addWidget(self._chk_publications)
        rl.addWidget(self._btn_restore)

        g_partial_r, self._restore_scope = scope_group_box(
            "Restauration partielle — composants à appliquer",
            for_restore=True,
        )
        pr = g_partial_r.layout()
        self._chk_partial_preserve_mysql = QCheckBox(
            "Conserver la connexion MySQL de ce poste (paths.json)"
        )
        self._chk_partial_preserve_mysql.setChecked(True)
        self._chk_partial_preserve_mysql.setToolTip(
            "Sans dump MySQL restauré, la connexion et les chemins actifs sont toujours conservés."
        )
        def update_partial_connection_choice(mysql_selected):
            if not mysql_selected:
                self._chk_partial_preserve_mysql.setChecked(True)
            self._chk_partial_preserve_mysql.setEnabled(mysql_selected)
        self._restore_scope.chk_mysql.toggled.connect(update_partial_connection_choice)
        update_partial_connection_choice(self._restore_scope.chk_mysql.isChecked())
        self._chk_partial_keep_accounts = QCheckBox(
            "Conserver les comptes partagés de cette base (utilisateurs, rôles et mots de passe)"
        )
        self._chk_partial_keep_accounts.setChecked(True)
        self._chk_partial_preserve_audit = QCheckBox(
            "Conserver le journal d'audit (MySQL + miroirs locaux sensibles)"
        )
        self._chk_partial_preserve_audit.setChecked(True)
        self._chk_partial_preserve_audit.setText("Conserver le journal courant de cette base (tous les postes) et les miroirs locaux")
        self._lbl_partial_archive = QLabel("Archive : (aucune)")
        self._lbl_partial_archive.setWordWrap(True)
        self._lbl_partial_archive.setObjectName("settingsPathsHint")
        self._btn_load_archive = QPushButton("Choisir l'archive et adapter les cases…")
        self._btn_load_archive.clicked.connect(self._on_load_partial_archive)
        self._btn_restore_partial = QPushButton("Restaurer partiellement la sélection")
        self._btn_restore_partial.setObjectName("primaryButton")
        self._btn_restore_partial.clicked.connect(self._on_restore_partial)
        pr.addWidget(self._chk_partial_preserve_mysql)
        pr.addWidget(self._chk_partial_keep_accounts)
        pr.addWidget(self._chk_partial_preserve_audit)
        pr.addWidget(self._lbl_partial_archive)
        pr.addWidget(self._btn_load_archive)
        pr.addWidget(self._btn_restore_partial)

        self._btn_inspect = QPushButton("Inspecter une archive…")
        self._btn_inspect.clicked.connect(self._on_inspect)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(200)
        self._log.setPlaceholderText("Journal des opérations…")

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setSpacing(16)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(header)
        lay.addWidget(self._health_lbl)
        lay.addWidget(g_backup)
        lay.addWidget(g_partial_b)
        lay.addWidget(g_restore)
        lay.addWidget(g_partial_r)
        monthly = QGroupBox("Sauvegarde et restauration d'un seul mois")
        monthly_layout = QVBoxLayout(monthly)
        monthly_hint = QLabel("Sauvegarder les situations et événements d'un mois MySQL, avec les identités "
            "et références nécessaires. Restaurer ce mois sans remplacer les autres mois ni les comptes. "
            "Les médias, documents exportés et réglages globaux ne sont pas inclus.")
        monthly_hint.setWordWrap(True)
        monthly_layout.addWidget(monthly_hint)
        self._btn_backup_month = QPushButton("Sauvegarder un mois...")
        self._btn_restore_month = QPushButton("Restaurer un mois...")
        self._btn_backup_month.clicked.connect(self._on_backup_month)
        self._btn_restore_month.clicked.connect(self._on_restore_month)
        monthly_layout.addWidget(self._btn_backup_month)
        monthly_layout.addWidget(self._btn_restore_month)
        lay.addWidget(monthly)
        lay.addWidget(self._btn_inspect)
        reset_group = QGroupBox("Réinitialisation complète")
        reset_layout = QVBoxLayout(reset_group)
        reset_hint = QLabel("Remettre a zero les données de tous les mois. Conservation des utilisateurs "
                            "et de l'historique des documents au choix. Sauvegarde préalable obligatoire.")
        reset_hint.setWordWrap(True)
        reset_layout.addWidget(reset_hint)
        self._btn_reset = QPushButton("Réinitialiser l'application...")
        self._btn_reset.clicked.connect(self._on_reset_application)
        reset_layout.addWidget(self._btn_reset)
        lay.addWidget(reset_group)
        maintenance_bar = QHBoxLayout()
        self._btn_maintenance_status = QPushButton("État de maintenance…")
        self._btn_maintenance_recover = QPushButton("Lever une maintenance interrompue…")
        self._btn_maintenance_status.clicked.connect(self._show_maintenance_status)
        self._btn_maintenance_recover.clicked.connect(self._recover_maintenance)
        maintenance_bar.addWidget(self._btn_maintenance_status)
        maintenance_bar.addWidget(self._btn_maintenance_recover)
        lay.addLayout(maintenance_bar)
        lay.addWidget(QLabel("<b>Journal</b>", textFormat=Qt.TextFormat.RichText))
        lay.addWidget(self._log)
        lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.addWidget(scroll)

    def on_page_activated(self) -> None:
        can_restore = session_can_perform_action("system.restore")
        for button in (self._btn_restore, self._btn_restore_partial,
                       self._btn_load_archive, self._btn_maintenance_recover, self._btn_reset, self._btn_restore_month):
            button.setEnabled(can_restore)
            button.setToolTip("" if can_restore else "La restauration est réservée aux administrateurs.")
        self._btn_backup_month.setEnabled(session_can_perform_action("system.backup"))
        self._refresh_health_async(force=False)
        self._load_mysql_tables_async()

    def _maintenance_state(self):
        if not session_can_perform_action("system.backup"):
            QMessageBox.warning(self, "Droits", "Permission maintenance refusée.")
            return None
        from sme_gend.services.database_maintenance import maintenance_status
        return run_with_progress(self, "Maintenance", "Lecture de l'état partagé…", lambda: maintenance_status(self._settings))

    def _show_maintenance_status(self):
        try:
            state = self._maintenance_state()
            if state is not None:
                QMessageBox.information(self, "Maintenance centralisée", "Écritures bloquées pour cette base : " + state['reason'] if state['active'] else "Aucune maintenance active sur cette base.")
        except Exception as exc:
            QMessageBox.warning(self, "Maintenance", str(exc))

    def _recover_maintenance(self):
        if not session_can_perform_action("system.restore"):
            QMessageBox.warning(self, "Droits", "Permission de restauration refusée.")
            return
        from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack
        from sme_gend.services.database_maintenance import end_interrupted_maintenance
        try:
            state = self._maintenance_state()
            if state is None:
                return
            if not state['active']:
                QMessageBox.information(self, "Maintenance", "Aucune maintenance à lever.")
                return
            if not confirm_destructive_with_ack(self, "Reprendre les écritures",
                "N'autorisez la reprise qu'après vérification du journal de récupération et de l'état de la base. Une restauration encore active ne peut pas être interrompue par ce bouton.",
                ack_label="J'ai vérifié la récupération et l'état de la base", confirm_label="Reprendre les écritures"):
                return
            def resume():
                end_interrupted_maintenance(self._settings, expected_token=state['token'], recovery_verified=True)
                return True
            if run_with_progress(self, "Maintenance", "Reprise contrôlée…", resume) is not True:
                return
            QMessageBox.information(self, "Maintenance", "Écritures réautorisées ; reprise enregistrée dans le journal.")
        except Exception as exc:
            QMessageBox.warning(self, "Maintenance", str(exc))

    def _confirm_audit_replacement(self, preserve):
        if preserve:
            return True
        from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack
        return confirm_destructive_with_ack(self, "Remplacer le journal partagé",
            "Le journal actuel de cette base concerne tous les postes. Le remplacer par celui de l'archive retire de la base courante les traces postérieures à cette sauvegarde. Les copies de récupération restent disponibles.",
            ack_label="Je confirme le remplacement du journal partagé de tous les postes", confirm_label="Remplacer le journal")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if hasattr(event, "spontaneous") and event.spontaneous():
            return
        self.on_page_activated()

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._health_cache = None
        self._refresh_health_async(force=True)
        self._load_mysql_tables_async()

    def _load_mysql_tables_async(self) -> None:
        st = self._settings

        def work():
            return list_mysql_table_names(st)

        def done(tables: list[str]) -> None:
            self._backup_scope.set_all_mysql_tables(tables)
            self._restore_scope.set_all_mysql_tables(tables)

        run_in_background(self, work, done)

    def _refresh_health_async(self, *, force: bool) -> None:
        import time

        now = time.time()
        if not force and self._health_cache is not None:
            ts, ok, msg, bkp = self._health_cache
            if now - ts < self._health_ttl_sec:
                self._show_health(ok, msg, bkp)
                return

        self._health_lbl.setText("Vérification MySQL…")
        st = self._settings

        def work():
            ok, msg = check_mysql_connectivity(st)
            bkp = find_latest_backup_zip()
            return ok, msg, bkp

        def done(result: tuple) -> None:
            ok, msg, bkp = result
            self._health_cache = (time.time(), ok, msg, bkp)
            self._show_health(ok, msg, bkp)

        run_in_background(self, work, done)

    def _show_health(self, ok: bool, msg: str, bkp: Path | None) -> None:
        archives = list_backup_archives()
        bkp_txt = f"Dernière sauvegarde : {bkp.name}" if bkp else "Aucune sauvegarde ZIP trouvée"
        if len(archives) > 1:
            bkp_txt += f" ({len(archives)} archives au total)"
        self._lbl_backup_dir.setText(
            f"Dossier standard : <code>{backup_storage_dir()}</code>"
        )
        self._lbl_backup_dir.setTextFormat(Qt.TextFormat.RichText)
        color = "#2e7d32" if ok else "#c62828"
        self._health_lbl.setText(
            f'<span style="color:{color}"><b>MySQL</b> : {msg}</span><br/>{bkp_txt}'
        )
        self._health_lbl.setTextFormat(Qt.TextFormat.RichText)

    def _on_backup_quick(self) -> None:
        if not session_can_perform_action("system.backup"):
            QMessageBox.warning(self, "Droits", "Permission sauvegarde refusée.")
            return

        def work():
            path = create_backup_to_default_dir(self._settings)
            return path, inspect_backup(path)

        try:
            dest, manifest = run_with_progress(
                self,
                "Sauvegarde rapide",
                "Sauvegarde vers le dossier SME…",
                work,
            )
        except Exception as e:
            QMessageBox.critical(self, "Sauvegarde", str(e))
            return
        removed = prune_old_backups(keep=15, directory=dest.parent)
        self._last_backup = dest
        self._append_log(f"✓ Sauvegarde rapide : {dest}")
        for line in format_audit_journal_summary(manifest):
            self._append_log(f"  {line}")
        if removed:
            self._append_log(f"  {removed} ancienne(s) archive(s) supprimée(s) (rétention 15).")
        QMessageBox.information(self, "Sauvegarde", f"Archive créée :\n{dest}")
        self._refresh_health_async(force=True)

    def _append_log(self, text: str) -> None:
        self._log.append(text)

    def _on_backup(self) -> None:
        if not session_can_perform_action("system.backup"):
            QMessageBox.warning(self, "Droits", "Permission sauvegarde refusée.")
            return
        default = str(Path.home() / default_backup_filename())
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer la sauvegarde complète",
            default,
            "Archive SME (*.zip)",
        )
        if not path:
            return
        dest = Path(path)

        def work():
            return create_full_backup(self._settings, dest)

        try:
            manifest = run_with_progress(
                self,
                "Sauvegarde",
                "Sauvegarde complète en cours…",
                work,
            )
        except Exception as e:
            QMessageBox.critical(self, "Sauvegarde", str(e))
            return
        self._last_backup = dest
        self._log_manifest(manifest, dest.name)
        QMessageBox.information(
            self,
            "Sauvegarde",
            f"Archive complète créée.\n\n{dest}",
        )

    def _on_partial_backup(self, *, to_default_dir: bool) -> None:
        if not session_can_perform_action("system.backup"):
            QMessageBox.warning(self, "Droits", "Permission sauvegarde refusée.")
            return
        scope = self._backup_scope.backup_scope()
        if not any(
            (
                scope.include_mysql,
                scope.include_user_config,
                scope.include_app_data,
                scope.include_app_config,
                scope.include_media_photos,
                scope.include_media_grades,
                scope.include_publications,
            )
        ):
            QMessageBox.warning(
                self,
                "Sauvegarde partielle",
                "Cochez au moins un composant à sauvegarder.",
            )
            return
        if to_default_dir:
            dest = backup_storage_dir() / default_backup_filename(partial=True)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Enregistrer la sauvegarde partielle",
                str(Path.home() / default_backup_filename(partial=True)),
                "Archive SME (*.zip)",
            )
            if not path:
                return
            dest = Path(path)

        def work():
            return create_backup(self._settings, dest, scope=scope)

        try:
            manifest = run_with_progress(
                self,
                "Sauvegarde partielle",
                "Création de l'archive…",
                work,
            )
        except Exception as e:
            QMessageBox.critical(self, "Sauvegarde partielle", str(e))
            return
        self._last_backup = dest
        self._append_log(f"✓ Sauvegarde partielle : {dest.name}")
        for line in describe_backup_scope(scope):
            self._append_log(f"  · {line}")
        self._log_manifest(manifest, dest.name, partial=True)
        if to_default_dir:
            prune_old_backups(keep=15, directory=dest.parent)
        QMessageBox.information(
            self,
            "Sauvegarde partielle",
            f"Archive créée :\n{dest}\n\n" + "\n".join(describe_backup_scope(scope)),
        )

    def _log_manifest(self, manifest, name: str, *, partial: bool = False) -> None:
        total = sum(manifest.table_counts.values())
        kind = manifest.backup_kind if hasattr(manifest, "backup_kind") else "full"
        self._append_log(
            f"  {kind} — {name} — {len(manifest.table_counts)} tables SQL, "
            f"{total} lignes, {manifest.personnel_media_files} médias"
        )
        if manifest.personnel_media_photos or manifest.personnel_media_grades:
            self._append_log(
                f"    médias : {manifest.personnel_media_photos} photo(s), "
                f"{manifest.personnel_media_grades} insigne(s)"
            )

    def _on_inspect(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Inspecter une sauvegarde",
            str(Path.home()),
            "Archive SME (*.zip)",
        )
        if not path:
            return
        self._show_inspect(Path(path))

    def _show_inspect(self, zip_p: Path) -> None:
        try:
            m = inspect_backup(zip_p)
            with zipfile.ZipFile(zip_p, "r") as zf:
                contents = archive_contents_summary(m, zf.namelist())
        except Exception as e:
            QMessageBox.warning(self, "Archive", str(e))
            return
        total = sum(m.table_counts.values())
        lines = [
            f"Type : {m.backup_kind}",
            f"Date : {m.created_at}",
            f"MySQL source : {m.mysql_database} ({m.mysql_host})",
        ]
        if contents["mysql"]:
            lines.append(f"Tables SQL : {len(m.table_counts)} — {total} lignes")
        for line in describe_backup_scope(m.saved_scope()):
            lines.append(f"  · {line}")
        lines.extend(format_audit_journal_summary(m))
        if m.personnel_media_files:
            lines.append(
                f"Médias : {m.personnel_media_files} fichier(s) "
                f"({m.personnel_media_photos} photos, {m.personnel_media_grades} insignes)"
            )
        if m.app_data_files:
            lines.append(f"Fichiers data\\ : {', '.join(m.app_data_files)}")
        if m.publication_files:
            lines.append(f"Versions archivées SME : {len(m.publication_files) // 4}")
        self._append_log("Inspection " + zip_p.name + ":\n" + "\n".join(lines))
        QMessageBox.information(self, "Contenu de l'archive", "\n".join(lines))

    def _on_load_partial_archive(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Archive pour restauration partielle",
            str(
                self._partial_restore_zip.parent
                if self._partial_restore_zip
                else (self._last_backup.parent if self._last_backup else Path.home())
            ),
            "Archive SME (*.zip)",
        )
        if not path:
            return
        zip_p = Path(path)
        self.select_archive_for_restore(zip_p)

    def select_archive_for_restore(self, zip_p: Path) -> None:
        """Prépare la sélection, sans déclencher une restauration."""
        if not session_can_perform_action("system.restore"):
            return
        zip_p = Path(zip_p).resolve()
        self._partial_restore_zip = None
        self._lbl_partial_archive.setText("Archive non sélectionnée : vérification en cours.")
        try:
            m = inspect_backup(zip_p)
            with zipfile.ZipFile(zip_p, "r") as zf:
                hints = archive_contents_summary(m, zf.namelist())
        except Exception as e:
            QMessageBox.warning(self, "Archive", str(e))
            return
        self._partial_restore_zip = zip_p
        self._restore_scope.apply_archive_hints(
            has_mysql=hints["mysql"],
            has_user=hints["user_config"],
            has_data=hints["app_data"],
            has_publications=m.has_publications(),
            has_appcfg=hints["app_config"],
            has_media=hints["media"],
            saved=m.saved_scope(),
        )
        self._lbl_partial_archive.setText(f"Archive : {zip_p.name} ({m.backup_kind})")

    def _on_restore_full(self) -> None:
        if not session_can_perform_action("system.restore"):
            QMessageBox.warning(self, "Droits", "Permission restauration refusée.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Restaurer une sauvegarde complète",
            str(self._last_backup.parent if self._last_backup else Path.home()),
            "Archive SME (*.zip)",
        )
        if not path:
            return
        zip_p = Path(path)
        from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack

        from PySide6.QtWidgets import QInputDialog
        mode, accepted = QInputDialog.getItem(self, 'Contenu à restaurer',
            'Que souhaitez-vous restaurer depuis cette sauvegarde ?',
            ['Choisir les mois présents dans la sauvegarde', 'Toute la sauvegarde'], 0, False)
        if not accepted:
            return
        if mode == 'Choisir les mois présents dans la sauvegarde':
            self._restore_selected_months(zip_p)
            return

        preserve_audit = self._chk_preserve_audit.isChecked()
        if not self._confirm_audit_replacement(preserve_audit):
            return
        keep_accounts = self._chk_keep_accounts.isChecked()
        confirm_msg = (
            f"Remplacer les données par le contenu complet de :\n{zip_p.name} ?\n\n"
            "Fermez tous les autres postes et suspendez les saisies pendant la maintenance. "
            "Les données seront préparées et contrôlées avant la bascule ; "
            "l'ancien état sera conservé pour récupération.\n\n"
            "La restauration complète remet aussi les historiques des dossiers à l'état de la sauvegarde. "
            "Pour conserver les passations saisies depuis, choisissez la restauration par mois."
        )
        if keep_accounts:
            confirm_msg += (
                "\n\nLes comptes partagés de cette base (utilisateurs, rôles, mots de passe) "
                "seront conservés."
            )
        if preserve_audit:
            confirm_msg += (
                "\n\nLe journal d'audit MySQL et les miroirs locaux sensibles "
                "de cette base seront conservés."
            )
        if not confirm_destructive_with_ack(
            self,
            "Confirmer la restauration complète",
            confirm_msg,
            ack_label="Je confirme la fermeture des autres postes et la suspension des saisies",
            confirm_label="Restaurer",
        ):
            return

        def work():
            return restore_full_backup(
                self._settings,
                zip_p,
                restore_user_config=self._chk_config.isChecked(),
                restore_app_data=self._chk_data.isChecked(),
                restore_publications=self._chk_publications.isChecked(),
                restore_personnel_media=self._chk_media.isChecked(),
                preserve_mysql_connection=self._chk_mysql.isChecked(),
                preserve_audit_journal=preserve_audit,
                preserve_local_accounts=keep_accounts,
                maintenance_confirmed=True,
            )

        self._finish_restore(work, zip_p.name, preserve_audit, keep_accounts)

    def _restore_selected_months(self, archive):
        from PySide6.QtWidgets import QDialog
        from sme_gend.ui.backup_months_dialog import BackupMonthsDialog
        from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack
        from sme_gend.services.backup_month_selection import prepare_backup_months, restore_selected_backup_months
        prepared = run_with_progress(self, 'Lecture de la sauvegarde',
            'Vérification de l’archive et lecture des mois présents…',
            lambda: prepare_backup_months(self._settings, archive))
        if prepared is None:
            return
        dialog = BackupMonthsDialog(prepared.months, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_months()
        if not selected:
            return
        if not confirm_destructive_with_ack(self, 'Confirmer les mois à restaurer',
            f'Base cible : {self._settings.mysql_host}/{self._settings.mysql_database}\n'
            f'Archive : {archive.name}\nMois : ' + ', '.join(selected) + '\n\n'
            'Les données de ces mois seront remplacées. Les autres mois et les comptes seront conservés. '
            'Une sauvegarde de sécurité sera créée. Fermez les autres postes pendant la restauration.',
            ack_label='Je confirme les mois sélectionnés et la fermeture des autres postes',
            confirm_label='Restaurer ces mois'):
            return
        result = run_with_progress(self, 'Restauration des mois sélectionnés',
            'Sauvegarde de sécurité puis restauration…',
            lambda: restore_selected_backup_months(self._settings, prepared, selected))
        if result is None:
            return
        self._append_log('Mois restaurés : ' + ', '.join(result['months']))
        QMessageBox.information(self, 'Restauration terminée',
            'Mois restaurés : ' + ', '.join(result['months']) +
            f'\nGrades recalculés depuis le dossier : {result.get("grade_history_reapplied", 0)}' +
            '\nSauvegarde préalable : ' + result['backup'])
        window = self.window()
        if hasattr(window, 'refresh_for_month_change'):
            window.refresh_for_month_change()

    def _on_restore_partial(self) -> None:
        if not session_can_perform_action("system.restore"):
            QMessageBox.warning(self, "Droits", "Permission restauration refusée.")
            return
        if self._partial_restore_zip is None:
            QMessageBox.information(
                self,
                "Restauration partielle",
                "Choisissez d'abord une archive (« Choisir l'archive… »).",
            )
            return
        zip_p = self._partial_restore_zip
        scope = self._restore_scope.restore_scope(
            preserve_mysql=self._chk_partial_preserve_mysql.isChecked(),
            preserve_audit=self._chk_partial_preserve_audit.isChecked(),
            preserve_local_accounts=self._chk_partial_keep_accounts.isChecked(),
        )
        if not self._confirm_audit_replacement(scope.preserve_audit_journal):
            return
        if not any(
            (
                scope.restore_mysql,
                scope.restore_user_config,
                scope.restore_app_data,
                scope.restore_app_config,
                scope.restore_media_photos,
                scope.restore_media_grades,
                scope.restore_publications,
            )
        ):
            QMessageBox.warning(self, "Restauration", "Cochez au moins un composant.")
            return
        from sme_gend.ui.confirm_dialogs import confirm_destructive_with_ack

        detail = "\n".join(
            x
            for x in [
                "MySQL" if scope.restore_mysql else None,
                "Config utilisateur" if scope.restore_user_config else None,
                "data\\" if scope.restore_app_data else None,
                "app_config" if scope.restore_app_config else None,
                "Photos" if scope.restore_media_photos else None,
                "Insignes" if scope.restore_media_grades else None,
                "Versions archivées SME" if scope.restore_publications else None,
            ]
            if x
        )
        if not confirm_destructive_with_ack(
            self,
            "Restauration partielle",
            f"Appliquer depuis {zip_p.name} :\n{detail}\n\n"
            "Les composants non cochés ne seront pas modifiés.\n\n"
            "Sans dump MySQL, les chemins actuels du poste, sa connexion et le dossier partagé actif sont conservés. "
            "Changez de dossier séparément dans Configuration MySQL et chemins.\n\n"
            "Si MySQL est sélectionné, fermez les autres postes et suspendez les saisies.",
            ack_label="Je confirme les composants choisis et la maintenance MySQL si sélectionnée",
            confirm_label="Restaurer partiellement la sélection",
        ):
            return

        from dataclasses import replace
        scope = replace(scope, maintenance_confirmed=True)

        def work():
            return restore_backup(self._settings, zip_p, scope=scope)

        self._finish_restore(
            work,
            zip_p.name,
            scope.preserve_audit_journal,
            scope.preserve_local_accounts,
        )

    def _finish_restore(
        self,
        work,
        zip_name: str,
        preserve_audit: bool,
        keep_accounts: bool = False,
    ) -> None:
        try:
            result = run_with_progress(self, "Restauration", "Restauration en cours…", work)
            if result is None:
                self._append_log("✗ Restauration interrompue ; consultez le message d’erreur et les copies de récupération.")
                return
        except Exception as e:
            QMessageBox.critical(self, "Restauration", str(e))
            self._append_log(f"✗ Échec : {e}")
            return
        self._append_log(f"✓ Restauration terminée depuis {zip_name}")
        if preserve_audit:
            self._append_log("  Journal courant conservé pour tous les postes de cette base.")
        if keep_accounts:
            self._append_log("  Comptes de ce poste conservés.")
        win = self.window()
        QMessageBox.information(
            self,
            "Restauration",
            "Restauration terminée.\n\n"
            "Les copies de récupération sont conservées dans le dossier des sauvegardes, "
            "sous restore_sessions.\n\n"
            "Rechargez la configuration puis validez les données avant de rouvrir les autres postes.",
        )
        if hasattr(win, "reload_runtime_configuration"):
            win.reload_runtime_configuration(show_success_dialog=False)
        if hasattr(win, "refresh_for_month_change"):
            win.refresh_for_month_change()

    def _on_reset_application(self):
        if not session_can_perform_action("system.restore"):
            QMessageBox.warning(self, "Droits", "Réinitialisation réservée aux administrateurs.")
            return
        from PySide6.QtWidgets import QApplication, QDialog, QInputDialog
        from sme_gend.ui.application_reset_dialog import ApplicationResetDialog
        from sme_gend.services.application_reset import reset_application, reset_target
        target = reset_target(self._settings)
        dialog = ApplicationResetDialog(target, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        typed, accepted = QInputDialog.getText(self, "Confirmer la réinitialisation",
            "Cette action efface toutes les données de la base sélectionnée.\n"
            "Pour confirmer, recopiez exactement :\n" + target)
        if not accepted or typed != target:
            return
        options = dialog.options()
        username, password = dialog.username.text(), dialog.password.text()
        try:
            result = run_with_progress(self, "Réinitialisation complète",
                "Sauvegarde puis réinitialisation de la base...",
                lambda: reset_application(self._settings, options, confirmation=typed,
                    new_username=username, new_password=password))
            if result is None:
                return
        except Exception as exc:
            QMessageBox.critical(self, "Réinitialisation interrompue", str(exc))
            return
        QMessageBox.information(self, "Réinitialisation terminée",
            "La base a été réinitialisée. Les sessions doivent se reconnecter.\n"
            "Sauvegarde : " + result['backup'] + "\nRecuperation des fichiers : " + result['recovery'] +
            "\n" + "\n".join(result['warnings']) + "\nRelancez l'application pour continuer.")
        QApplication.quit()

    def _on_backup_month(self):
        if not session_can_perform_action("system.backup"):
            QMessageBox.warning(self,"Droits","Permission de sauvegarde refusée.")
            return
        from PySide6.QtWidgets import QInputDialog
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService
        from sme_gend.services.month_backup import create_month_backup
        try:
            months = run_with_progress(self,"Sauvegarde mensuelle","Lecture des mois MySQL...",
                lambda:SmeWorkspaceService(self._settings).list_mysql_months())
            if not months:
                QMessageBox.information(self,"Sauvegarde mensuelle","Aucun mois MySQL disponible.")
                return
            month,ok=QInputDialog.getItem(self,"Mois a sauvegarder","Mois MySQL :",sorted(months,reverse=True),0,False)
            if not ok:return
            destination,_=QFileDialog.getSaveFileName(self,"Sauvegarder un mois",
                str(backup_storage_dir()/f"SME_mois_{month}.zip"),"Sauvegarde mensuelle (*.zip)")
            if not destination:return
            result=run_with_progress(self,"Sauvegarde mensuelle","Sauvegarde du mois "+month+"...",
                lambda:create_month_backup(self._settings,month,Path(destination)))
            if result is None:return
            self._append_log("Sauvegarde mensuelle : "+month+" > "+destination)
            QMessageBox.information(self,"Sauvegarde mensuelle","Mois sauvegarde : "+month+"\n"+destination)
        except Exception as exc:
            QMessageBox.critical(self,"Sauvegarde mensuelle",str(exc))

    def _on_restore_month(self):
        if not session_can_perform_action("system.restore"):
            QMessageBox.warning(self,"Droits","Restauration réservée aux administrateurs.")
            return
        from PySide6.QtWidgets import QInputDialog
        from sme_gend.services.month_backup import inspect_month_backup,restore_month_backup
        archive,_=QFileDialog.getOpenFileName(self,"Choisir une sauvegarde mensuelle",str(backup_storage_dir()),"Sauvegarde mensuelle (*.zip)")
        if not archive:return
        try:
            payload=run_with_progress(self,"Archive mensuelle","Vérification de l'archive...",lambda:inspect_month_backup(archive))
            if payload is None:return
            month=payload['month']
            target=f"{self._settings.mysql_host}:{self._settings.mysql_port}/{self._settings.mysql_database}"
            count=len(payload['tables'].get('carriere_situation',[]))
            typed,ok=QInputDialog.getText(self,"Restaurer le mois "+month,
                f"Base cible : {target}\nMois : {month} ; {count} situation(s).\n"
                "Les données de ce mois seront remplacées. Les autres mois et comptes seront conservés.\n"
                "Les passations du dossier seront conservées et réappliquées selon leurs dates.\n"
                "Une sauvegarde complete sera créée avant toute suppression.\nPour confirmer, saisissez : "+month)
            if not ok or typed!=month:return
            result=run_with_progress(self,"Restauration mensuelle","Sauvegarde de sécurité puis restauration du mois...",
                lambda:restore_month_backup(self._settings,archive,confirmed_month=typed))
            if result is None:return
            QMessageBox.information(self,"Restauration mensuelle","Mois restauré : "+month+"\nSauvegarde préalable : "+result['backup'])
            self._append_log("Restauration du mois "+month)
            win=self.window()
            if hasattr(win,"refresh_for_month_change"):win.refresh_for_month_change()
            self._health_cache=None
        except Exception as exc:
            QMessageBox.critical(self,"Restauration mensuelle",str(exc))
