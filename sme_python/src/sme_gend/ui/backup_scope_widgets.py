"""Widgets partagés sauvegarde / restauration partielle."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.backup_restore_service import BackupScope, RestoreScope


class _ScopeCheckboxes(QWidget):
    def __init__(self, *, for_restore: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._for_restore = for_restore
        prefix = "restore" if for_restore else "include"

        self.chk_mysql = QCheckBox("MySQL (dump SQL)")
        self.chk_mysql.setChecked(True)
        self.chk_user = QCheckBox("Configuration utilisateur (paths, thème, contexte SME)")
        self.chk_user.setChecked(True)
        self.chk_data = QCheckBox("Fichiers data\\ (JSON / journaux locaux)")
        self.chk_data.setChecked(not for_restore)
        self.chk_publications = QCheckBox("Versions archivées des classeurs SME")
        self.chk_publications.setChecked(True)
        self.chk_appcfg = QCheckBox("Table app_config (export JSON)")
        self.chk_appcfg.setChecked(True)
        self.chk_photos = QCheckBox("Médias — photos du personnel")
        self.chk_photos.setChecked(True)
        self.chk_grades = QCheckBox("Médias — insignes / grades")
        self.chk_grades.setChecked(True)

        self._btn_tables = QPushButton("Tables MySQL…")
        self._btn_tables.setToolTip(
            "Limiter le dump ou la restauration à certaines tables "
            "(laisser vide = toutes celles de l'archive)."
        )
        self._btn_tables.clicked.connect(self._pick_tables)
        self._mysql_tables: tuple[str, ...] | None = None
        self._all_tables: list[str] = []
        self._lbl_tables = QLabel("Tables : toutes")
        self._lbl_tables.setWordWrap(True)
        self._lbl_tables.setObjectName("settingsPathsHint")

        row = QHBoxLayout()
        row.addWidget(self._btn_tables)
        row.addWidget(self._lbl_tables, stretch=1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.chk_mysql)
        lay.addLayout(row)
        lay.addWidget(self.chk_user)
        lay.addWidget(self.chk_data)
        lay.addWidget(self.chk_publications)
        lay.addWidget(self.chk_appcfg)
        lay.addWidget(self.chk_photos)
        lay.addWidget(self.chk_grades)

    def set_all_mysql_tables(self, tables: list[str]) -> None:
        self._all_tables = list(tables)

    def _pick_tables(self) -> None:
        if not self._all_tables:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Tables MySQL")
        dlg.setMinimumSize(420, 420)
        lst = QListWidget()
        lst.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        checks: list[QListWidgetItem] = []
        selected = set(self._mysql_tables or [])
        for name in self._all_tables:
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(
                Qt.CheckState.Checked
                if (not selected or name in selected)
                else Qt.CheckState.Unchecked
            )
            lst.addItem(it)
            checks.append(it)
        presets = QHBoxLayout()
        for label, key in (
            ("Référentiels", "referentiels"),
            ("Carrière", "carriere"),
            ("app_config", "app_config"),
            ("Audit", "audit"),
        ):
            btn = QPushButton(label)

            def _apply(k=key, b=btn) -> None:
                from sme_gend.services.backup_restore_service import expand_mysql_preset

                pick = set(expand_mysql_preset(k, self._all_tables))
                for it in checks:
                    it.setCheckState(
                        Qt.CheckState.Checked
                        if it.text() in pick
                        else Qt.CheckState.Unchecked
                    )

            btn.clicked.connect(_apply)
            presets.addWidget(btn)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("Cochez les tables à inclure (aucune = toutes)."))
        v.addLayout(presets)
        v.addWidget(lst)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        picked = [it.text() for it in checks if it.checkState() == Qt.CheckState.Checked]
        if not picked or len(picked) == len(self._all_tables):
            self._mysql_tables = None
            self._lbl_tables.setText("Tables : toutes")
        else:
            self._mysql_tables = tuple(sorted(picked))
            self._lbl_tables.setText(
                f"Tables : {len(picked)} sélectionnée(s)"
            )

    def backup_scope(self) -> BackupScope:
        include_mysql = self.chk_mysql.isChecked()
        return BackupScope(
            include_mysql=include_mysql,
            mysql_tables=self._mysql_tables if include_mysql else None,
            include_user_config=self.chk_user.isChecked(),
            include_app_data=self.chk_data.isChecked(),
            include_app_config=self.chk_appcfg.isChecked(),
            include_media_photos=self.chk_photos.isChecked(),
            include_media_grades=self.chk_grades.isChecked(),
            include_publications=self.chk_publications.isChecked(),
        )

    def restore_scope(
        self,
        *,
        preserve_mysql: bool,
        preserve_audit: bool,
        preserve_local_accounts: bool = True,
    ) -> RestoreScope:
        restore_mysql = self.chk_mysql.isChecked()
        return RestoreScope(
            restore_mysql=restore_mysql,
            mysql_tables=self._mysql_tables if restore_mysql else None,
            restore_user_config=self.chk_user.isChecked(),
            restore_app_data=self.chk_data.isChecked(),
            restore_app_config=self.chk_appcfg.isChecked(),
            restore_media_photos=self.chk_photos.isChecked(),
            restore_media_grades=self.chk_grades.isChecked(),
            preserve_mysql_connection=preserve_mysql,
            preserve_audit_journal=preserve_audit,
            preserve_local_accounts=preserve_local_accounts,
            restore_publications=self.chk_publications.isChecked(),
        )

    def apply_archive_hints(
        self,
        *,
        has_mysql: bool,
        has_user: bool,
        has_data: bool,
        has_appcfg: bool,
        has_media: bool,
        saved: BackupScope | None,
        has_publications: bool = False,
    ) -> None:
        self.chk_mysql.setEnabled(has_mysql)
        self.chk_user.setEnabled(has_user)
        self.chk_data.setEnabled(has_data)
        self.chk_appcfg.setEnabled(has_appcfg)
        self.chk_photos.setEnabled(has_media)
        self.chk_grades.setEnabled(has_media)
        self.chk_publications.setEnabled(has_publications)
        self.chk_publications.setChecked(has_publications and (saved is None or saved.include_publications))
        if not has_mysql:
            self.chk_mysql.setChecked(False)
        if saved:
            self.chk_mysql.setChecked(saved.include_mysql and has_mysql)
            self.chk_user.setChecked(saved.include_user_config and has_user)
            self.chk_data.setChecked(saved.include_app_data and has_data)
            self.chk_appcfg.setChecked(saved.include_app_config and has_appcfg)
            self.chk_photos.setChecked(saved.include_media_photos and has_media)
            self.chk_grades.setChecked(saved.include_media_grades and has_media)
            if saved.mysql_tables:
                self._mysql_tables = saved.mysql_tables
                self._lbl_tables.setText(
                    f"Tables : {len(saved.mysql_tables)} (archive)"
                )


def scope_group_box(title: str, *, for_restore: bool = False) -> tuple[QGroupBox, _ScopeCheckboxes]:
    g = QGroupBox(title)
    w = _ScopeCheckboxes(for_restore=for_restore)
    lay = QVBoxLayout(g)
    lay.addWidget(w)
    return g, w
