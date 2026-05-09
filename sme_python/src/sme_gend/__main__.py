"""Point d'entrée : python -m sme_gend."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def _setup_logging() -> None:
    """Configure le logging applicatif vers data/sme_app.log (rotation 1 Mo × 3)."""
    # Résolution tardive pour éviter les imports circulaires au niveau module
    try:
        from sme_gend.config.settings import project_root
        log_dir = project_root() / "data"
    except Exception:
        log_dir = Path(__file__).resolve().parents[3] / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sme_app.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)

    def _excepthook(exc_type: type, exc_value: BaseException, exc_tb: object) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)  # type: ignore[arg-type]
            return
        logging.getLogger("sme_gend").critical(
            "Exception non interceptée",
            exc_info=(exc_type, exc_value, exc_tb),  # type: ignore[arg-type]
        )

    sys.excepthook = _excepthook  # type: ignore[assignment]


def main() -> int:
    _setup_logging()
    _log = logging.getLogger("sme_gend")
    _log.info("Démarrage SME Gendarmerie")

    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from sme_gend.config import get_settings
    from sme_gend.services.dashboard_service import DashboardService
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.detaches_config_service import DetachesConfigService
    from sme_gend.services.detaches_preview_service import DetachesPreviewService
    from sme_gend.services.numerique_preview_service import NumeriquePreviewService
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.ui.config_paths_dialog import ConfigPathsDialog
    from sme_gend.ui.resources import load_app_icon
    from sme_gend.ui.shell_window import ShellWindow
    from sme_gend.ui.sme_session_dialog import run_startup_sme_session_dialog
    from sme_gend.ui.theme import apply_app_theme

    from sme_gend.config.sme_context import load_sme_context as _load_theme_ctx

    app = QApplication(sys.argv)
    app.setApplicationName("SME Gendarmerie")
    app.setApplicationDisplayName("SME — Effectifs")

    # Traduction Qt → boutons standard ("Oui/Non", "Annuler", "Fermer", "Appliquer"…)
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    _qt_tr = QTranslator(app)
    _tr_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    _qt_tr.load(QLocale(QLocale.Language.French), "qtbase", "_", _tr_path)
    app.installTranslator(_qt_tr)

    apply_app_theme(app, _load_theme_ctx().theme)
    app.setWindowIcon(load_app_icon())

    settings = None
    while settings is None:
        try:
            settings = get_settings()
        except FileNotFoundError as e:
            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Critical)
            box.setWindowTitle("Base de données")
            box.setText(str(e))
            box.setInformativeText(
                "Vous pouvez activer le <b>mode SQLite</b> (données principales dans le .sqlite) et n’utiliser "
                "Access que pour l’import, ou indiquer le chemin du .mdb (réseau possible). "
                "Les variables d’environnement (SMEDB_PATH, SME_SQLITE_LISTE_PATH, …) restent prioritaires."
            )
            btn_cfg = box.addButton(
                "Configurer le chemin…",
                QMessageBox.ButtonRole.ActionRole,
            )
            box.addButton("Quitter", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() != btn_cfg:
                return 1
            dlg = ConfigPathsDialog(None, settings=None, focus_database=True)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return 1

    personnel = PersonnelService(settings)
    referentiel = ReferentielService(settings)
    layout_cfg = LayoutConfigService(settings)
    liste_present = ListePresentService(settings, layout_cfg)
    dashboard = DashboardService(settings)
    numerique_preview = NumeriquePreviewService(settings)
    detaches_preview = DetachesPreviewService(settings)
    detaches_cfg = DetachesConfigService(settings)

    from sme_gend.config.sme_context import load_sme_context as _load_ctx
    if not _load_ctx().mois_actif:
        run_startup_sme_session_dialog(None, settings)

    window = ShellWindow(
        settings,
        personnel,
        referentiel,
        liste_present,
        layout_cfg,
        dashboard,
        numerique_preview,
        detaches_preview,
        detaches_cfg,
    )
    window.setWindowIcon(load_app_icon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
