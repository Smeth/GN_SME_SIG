"""Point d'entrée : python -m sme_gend."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _WindowsRotatingFileHandler(RotatingFileHandler):
    """Rotation fichier compatible Windows (fermeture du flux avant rename)."""

    def doRollover(self) -> None:
        if self.stream:
            self.stream.close()
            self.stream = None  # type: ignore[assignment]
        try:
            super().doRollover()
        except OSError:
            if not self.delay and self.stream is None:
                self.stream = self._open()


def _setup_logging() -> None:
    """Configure le logging applicatif vers data/sme_app.log (rotation 1 Mo × 3)."""
    try:
        from sme_gend.config.settings import project_root
        log_dir = project_root() / "data"
    except Exception:
        log_dir = Path(__file__).resolve().parents[3] / "data"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sme_app.log"

    handler = _WindowsRotatingFileHandler(
        log_path,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    from sme_gend.performance import default_log_level

    root_logger = logging.getLogger()
    root_logger.setLevel(default_log_level())
    if not any(
        isinstance(h, _WindowsRotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path)
        for h in root_logger.handlers
    ):
        root_logger.addHandler(handler)

    def _excepthook(exc_type: type, exc_value: BaseException, exc_tb: object) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)  # type: ignore[arg-type]
            return
        log = logging.getLogger("sme_gend")
        log.critical(
            "Exception non interceptée",
            exc_info=(exc_type, exc_value, exc_tb),  # type: ignore[arg-type]
        )
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox

            app = QApplication.instance()
            if app is not None:
                QMessageBox.critical(
                    None,
                    "Erreur fatale — S.G.I DPF",
                    f"{exc_type.__name__}: {exc_value}\n\n"
                    "Consultez data/sme_app.log pour le détail.",
                )
        except Exception:
            pass

    sys.excepthook = _excepthook  # type: ignore[assignment]


_splash: object | None = None

_CONFIGURE_MYSQL_LABEL = "Configurer MySQL…"


def _startup_step(log: logging.Logger, msg: str) -> None:
    global _splash
    if getattr(sys, "frozen", False):
        log.info(msg)
    if _splash is not None:
        from PySide6.QtWidgets import QApplication

        from sme_gend.ui.startup_splash import splash_show_status

        splash_show_status(_splash, msg)  # type: ignore[arg-type]
        app = QApplication.instance()
        if app is not None:
            app.processEvents()


def main() -> int:
    if "--self-test-report" in sys.argv:
        from sme_gend.services.portable_self_test import run_portable_self_test

        index = sys.argv.index("--self-test-report")
        if index + 1 >= len(sys.argv):
            return 2
        return run_portable_self_test(Path(sys.argv[index + 1]))
    _setup_logging()
    _log = logging.getLogger("sme_gend")
    _log.info("Démarrage S.G.I DPF")
    try:
        return _run_app(_log)
    except Exception:
        _log.exception("Échec au démarrage")
        return 1


def _open_mysql_config_dialog(settings, *, parent=None) -> bool:
    from PySide6.QtWidgets import QDialog

    from sme_gend.config import get_settings
    from sme_gend.ui.config_paths_dialog import ConfigPathsDialog

    dlg = ConfigPathsDialog(parent, settings=settings, focus_database=True)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    get_settings()  # recharge le cache interne si besoin
    return True


def _ensure_mysql_or_configure(app, settings, _log: logging.Logger):
    """Teste MySQL ; propose de reconfigurer si la connexion échoue. Retourne (ok, settings)."""
    from PySide6.QtWidgets import QMessageBox

    from sme_gend.config import get_settings
    from sme_gend.config.user_paths import user_has_mysql_profile, user_paths_config_file
    from sme_gend.services.mysql_health import format_mysql_error_for_user, probe_mysql, startup_mysql_help_html
    from sme_gend.ui.startup_splash import splash_hide_for_modal, splash_show_after_modal

    first_deploy = not user_has_mysql_profile()
    while True:
        _startup_step(_log, "Connexion MySQL…")
        probe = probe_mysql(settings)
        if probe.ok:
            _startup_step(_log, "MySQL OK")
            return True, settings
        _log.error("Connexion MySQL échouée : %s", probe.technical or probe.message)
        splash_hide_for_modal(_splash)  # type: ignore[arg-type]
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Connexion MySQL")
        if first_deploy:
            box.setText("Premier lancement — configurez la connexion MySQL.")
        else:
            box.setText("Impossible de joindre la base MySQL.")
        extra = startup_mysql_help_html(paths_file=str(user_paths_config_file()))
        box.setInformativeText(
            f"<p>{format_mysql_error_for_user(probe.message)}</p>{extra}"
        )
        btn_cfg = box.addButton(_CONFIGURE_MYSQL_LABEL, QMessageBox.ButtonRole.ActionRole)
        btn_retry = box.addButton("Réessayer", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Quitter", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        splash_show_after_modal(_splash, "Connexion MySQL…")  # type: ignore[arg-type]
        if clicked == btn_cfg:
            splash_hide_for_modal(_splash)  # type: ignore[arg-type]
            if _open_mysql_config_dialog(settings):
                settings = get_settings()
                first_deploy = not user_has_mysql_profile()
            splash_show_after_modal(_splash, "Connexion MySQL…")  # type: ignore[arg-type]
            continue
        if clicked == btn_retry:
            continue
        return False, settings


def _run_app(_log: logging.Logger) -> int:
    global _splash
    _startup_step(_log, "Import Qt…")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from sme_gend.config import get_settings
    from sme_gend.services.dashboard_service import DashboardService
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.detaches_config_service import DetachesConfigService
    from sme_gend.services.detaches_preview_service import DetachesPreviewService
    from sme_gend.services.numerique_preview_service import NumeriquePreviewService
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.ui.resources import load_app_icon
    from sme_gend.services.auth_service import AuthService
    from sme_gend.ui.login_dialog import LoginDialog
    from sme_gend.ui.sme_session_dialog import run_startup_sme_session_dialog
    from sme_gend.ui.theme import apply_app_theme, install_system_theme_watcher

    from sme_gend.config.sme_context import load_local_theme_preference

    _startup_step(_log, "Création QApplication…")
    app = QApplication(sys.argv)
    _startup_step(_log, "QApplication OK")

    import threading

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None:
            return
        _log.critical(
            "Exception non interceptée (thread %s)",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_excepthook



    from sme_gend.config.branding import APP_NAME, APP_NAME_FULL, WINDOW_TITLE
    from sme_gend.ui.startup_splash import create_startup_splash, splash_show_status

    _splash = create_startup_splash(f"{APP_NAME} — démarrage…")
    _splash.show()
    app.processEvents()
    from sme_gend.performance import apply_runtime_profile

    apply_runtime_profile(app)
    _startup_step(_log, "Import interface principale…")
    from sme_gend.ui.shell_window import ShellWindow
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME_FULL)

    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    _qt_tr = QTranslator(app)
    _tr_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    _qt_tr.load(QLocale(QLocale.Language.French), "qtbase", "_", _tr_path)
    app.installTranslator(_qt_tr)

    apply_app_theme(app, load_local_theme_preference())
    app.setWindowIcon(load_app_icon())
    _startup_step(_log, "Chargement configuration…")

    settings = get_settings()
    _startup_step(_log, "Configuration chargée")

    mysql_ok, settings = _ensure_mysql_or_configure(app, settings, _log)
    if not mysql_ok:
        return 1

    from sme_gend.ui.startup_maintenance import wait_for_startup_maintenance
    from sme_gend.ui.startup_splash import splash_hide_for_modal, splash_show_after_modal
    splash_hide_for_modal(_splash)
    if not wait_for_startup_maintenance(settings):
        _log.warning("Démarrage suspendu avant les écritures : maintenance ou état inaccessible.")
        return 1
    splash_show_after_modal(_splash, "Vérification de la configuration…")

    from sme_gend.services.app_config_store import ensure_central_config_ready

    try:
        ensure_central_config_ready(settings)
    except Exception as exc:
        _log.warning("Configuration centralisée : %s", exc)
    _startup_step(_log, "Config centralisée OK")

    from sme_gend.services.access_odbc import purge_stale_staged_copies

    try:
        purge_stale_staged_copies()
    except Exception as exc:
        _log.warning("Purge staging import : %s", exc)

    auth = AuthService()
    auth.reload_config()
    auth.bootstrap_default_admin_account()
    from sme_gend.services.auth_context import set_app_auth

    set_app_auth(auth)
    from sme_gend.ui.startup_splash import splash_hide_for_modal, splash_show_after_modal

    # Chargement lourd pendant le splash, avant la fenêtre de connexion :
    # après « Se connecter », l'interface s'affiche sans longue attente.
    _startup_step(_log, "Initialisation des services…")
    personnel = PersonnelService(settings)
    referentiel = ReferentielService(settings)
    layout_cfg = LayoutConfigService(settings)
    liste_present = ListePresentService(settings, layout_cfg)
    dashboard = DashboardService(settings)
    numerique_preview = NumeriquePreviewService(settings)
    detaches_preview = DetachesPreviewService(settings)
    detaches_cfg = DetachesConfigService(settings)

    _startup_step(_log, "Contexte SME — vérification après authentification…")
    _startup_step(_log, "Construction fenêtre principale…")
    app.processEvents()

    session = None
    try:
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
            auth_session=None,
        )
    except Exception as exc:
        _log.exception("Impossible de créer la fenêtre principale")
        splash_hide_for_modal(_splash)  # type: ignore[arg-type]
        QMessageBox.critical(
            None,
            "Démarrage SME",
            f"La fenêtre principale n'a pas pu être créée.\n\n{exc}\n\n"
            "Vérifiez la connexion MySQL et consultez data/sme_app.log.",
        )
        return 1

    if auth.is_enabled():
        _startup_step(_log, "Authentification — fenêtre de connexion")
        splash_hide_for_modal(_splash)  # type: ignore[arg-type]
        try:
            ok, session = LoginDialog.run_login(auth)
        finally:
            splash_show_after_modal(_splash, "Ouverture de l'application…")  # type: ignore[arg-type]
        if not ok:
            _log.info("Connexion annulée — arrêt.")
            return 0
        auth.mark_enforced()
        window.apply_login_session(session)

    from sme_gend.config.sme_context import load_sme_context as _load_ctx
    from sme_gend.ui.sme_session_dialog import run_startup_open_month_recovery

    splash_hide_for_modal(_splash)  # type: ignore[arg-type]
    try:
        if not run_startup_open_month_recovery(window, settings):
            _log.warning("Démarrage interrompu : résolution des périodes SME requise.")
            return 0
        ctx = _load_ctx()
        if not ctx.mois_actif:
            from sme_gend.services.workflow_permissions import session_can_perform_action
            if not session_can_perform_action("workflow.set_active_month"):
                QMessageBox.critical(window,"Mois de travail","Aucun mois de travail n’est ouvert. Demandez à un administrateur de définir le mois actif.")
                return 0
            run_startup_sme_session_dialog(window, settings)
        window.refresh_for_month_change()
    finally:
        splash_show_after_modal(_splash, "Ouverture de l’application…")  # type: ignore[arg-type]

    _startup_step(_log, "Authentification OK")

    window.setWindowIcon(load_app_icon())
    install_system_theme_watcher(app, window.apply_theme_from_preference)
    _startup_step(_log, "Affichage fenêtre principale")
    window.show()
    if _splash is not None:
        _splash.finish(window)
        _splash = None
    app.processEvents()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
