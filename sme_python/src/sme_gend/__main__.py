"""Point d'entrée : python -m sme_gend."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

    from sme_gend.config import get_settings
    from sme_gend.services.dashboard_service import DashboardService
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.ui.config_paths_dialog import ConfigPathsDialog
    from sme_gend.ui.resources import load_app_icon
    from sme_gend.ui.shell_window import ShellWindow
    from sme_gend.ui.theme import apply_app_theme

    app = QApplication(sys.argv)
    app.setApplicationName("SME Gendarmerie")
    app.setApplicationDisplayName("SME — Effectifs")
    apply_app_theme(app)
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
                "Vous pouvez indiquer le chemin du fichier Access (y compris un partage réseau) "
                "via le bouton ci‑dessous. Les variables SMEDB_PATH / SME_TEMPLATE_PATH restent prioritaires."
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

    window = ShellWindow(settings, personnel, referentiel, liste_present, layout_cfg, dashboard)
    window.setWindowIcon(load_app_icon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
