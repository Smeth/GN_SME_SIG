"""Dialogue : chemins base Access et maquette Excel (réseau / partage)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.config.settings import Settings
from sme_gend.config.user_paths import load_user_paths, save_user_paths, user_paths_config_file
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class ConfigPathsDialog(QDialog):
    """Enregistre les chemins dans %LOCALAPPDATA%\\SME_Gendarmerie\\paths.json."""

    def __init__(
        self,
        parent,
        settings: Settings | None = None,
        *,
        focus_database: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chemins des fichiers de données")
        self.setModal(True)
        self.setMinimumWidth(560)

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
        else:
            db_init = user.database_path or ""
            tpl_init = user.template_excel_path or ""
            tnum_init = user.template_numerique_path or ""
            tdet_init = user.template_detaches_path or ""

        hint = QLabel(
            "Priorité : variables d’environnement <b>SMEDB_PATH</b>, "
            "<b>SME_TEMPLATE_PATH</b>, <b>SME_NUMERIQUE_TEMPLATE_PATH</b>, "
            "<b>SME_DETACHES_PATH</b> (si définies), "
            "puis les chemins ci‑dessous. "
            "Les chemins réseau (UNC, ex. \\\\serveur\\partage\\fichier.mdb) sont acceptés."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)

        self._db = QLineEdit(db_init)
        self._db.setPlaceholderText("ex. C:\\Users\\…\\smegend.mdb ou \\\\serveur\\partage\\…")
        btn_db = QPushButton("Parcourir…")
        btn_db.clicked.connect(self._browse_db)
        row_db = QHBoxLayout()
        row_db.addWidget(self._db, stretch=1)
        row_db.addWidget(btn_db)

        self._tpl = QLineEdit(tpl_init)
        self._tpl.setPlaceholderText("Optionnel — maquette Excel pour la liste des présents")
        btn_tpl = QPushButton("Parcourir…")
        btn_tpl.clicked.connect(self._browse_tpl)
        row_tpl = QHBoxLayout()
        row_tpl.addWidget(self._tpl, stretch=1)
        row_tpl.addWidget(btn_tpl)

        self._tnum = QLineEdit(tnum_init)
        self._tnum.setPlaceholderText("Optionnel — gabarit situation numérique (numerique.xlsx)")
        btn_tnum = QPushButton("Parcourir…")
        btn_tnum.clicked.connect(self._browse_tnum)
        row_tnum = QHBoxLayout()
        row_tnum.addWidget(self._tnum, stretch=1)
        row_tnum.addWidget(btn_tnum)

        self._tdet = QLineEdit(tdet_init)
        self._tdet.setPlaceholderText("Optionnel — fichier officiel DETACHES.xlsx (module détachés)")
        btn_tdet = QPushButton("Parcourir…")
        btn_tdet.clicked.connect(self._browse_tdet)
        row_tdet = QHBoxLayout()
        row_tdet.addWidget(self._tdet, stretch=1)
        row_tdet.addWidget(btn_tdet)

        form = QFormLayout()
        form.addRow("Base Access (.mdb / .accdb) :", row_db)
        form.addRow("Maquette Excel (liste présents) :", row_tpl)
        form.addRow("Gabarit Excel (situation numérique) :", row_tnum)
        form.addRow("Fichier officiel détachés (DETACHES.xlsx) :", row_tdet)

        grp = QGroupBox("Fichiers")
        grp_lay = QVBoxLayout(grp)
        grp_lay.addLayout(form)

        cfg_path = QLabel(f"Enregistrement : <code>{user_paths_config_file()}</code>")
        cfg_path.setWordWrap(True)
        cfg_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(hint)
        lay.addWidget(grp)
        lay.addWidget(cfg_path)
        lay.addWidget(bb)

        if focus_database:
            self._db.setFocus()

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

    def _on_accept(self) -> None:
        db_raw = self._db.text().strip()
        tpl_raw = self._tpl.text().strip()
        tnum_raw = self._tnum.text().strip()
        tdet_raw = self._tdet.text().strip()

        if db_raw:
            p = Path(db_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Base Access",
                    f"Le fichier base n’existe pas ou n’est pas accessible :\n{p}",
                )
                return

        if tpl_raw:
            p = Path(tpl_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Maquette Excel",
                    f"Le fichier maquette n’existe pas ou n’est pas accessible :\n{p}",
                )
                return

        if tnum_raw:
            p = Path(tnum_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Gabarit numérique",
                    f"Le fichier gabarit n’existe pas ou n’est pas accessible :\n{p}",
                )
                return

        if tdet_raw:
            p = Path(tdet_raw)
            if not p.is_file():
                QMessageBox.warning(
                    self,
                    "Fichier détachés",
                    f"Le fichier DETACHES.xlsx n’existe pas ou n’est pas accessible :\n{p}",
                )
                return

        if not confirm_yes_no(
            self,
            "Enregistrer les chemins",
            "Enregistrer ces chemins dans le fichier de configuration utilisateur ?\n\n"
            "Un redémarrage de l'application est nécessaire pour appliquer les nouveaux chemins.",
            default_no=True,
        ):
            return

        save_user_paths(
            database_path=db_raw if db_raw else None,
            template_excel_path=tpl_raw if tpl_raw else None,
            template_numerique_path=tnum_raw if tnum_raw else None,
            template_detaches_path=tdet_raw if tdet_raw else None,
        )
        self.accept()
