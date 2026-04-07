"""Point d'entrée : python -m sme_gend."""

from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from sme_gend.config import get_settings
    from sme_gend.services.dashboard_service import DashboardService
    from sme_gend.services.layout_config_service import LayoutConfigService
    from sme_gend.services.liste_present_service import ListePresentService
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.ui.resources import load_app_icon
    from sme_gend.ui.shell_window import ShellWindow
    from sme_gend.ui.theme import apply_app_theme

    app = QApplication(sys.argv)
    app.setApplicationName("SME Gendarmerie")
    app.setApplicationDisplayName("SME — Effectifs")
    apply_app_theme(app)
    app.setWindowIcon(load_app_icon())

    try:
        settings = get_settings()
    except FileNotFoundError as e:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(
            None,
            "Base de données",
            f"{e}\n\nDéfinissez la variable d'environnement SMEDB_PATH vers le fichier .mdb.",
        )
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
