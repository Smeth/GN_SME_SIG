"""Vue SME DPF — édition du personnel DPF (comme Édition SME) + export gabarit officiel."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from sme_gend.config.settings import Settings
from sme_gend.services.sme_dpf_excel_export import (
    default_export_filename,
    export_sme_dpf_workbook,
    resolve_sme_dpf_template,
)
from sme_gend.services.sme_dpf_scope import (
    DPF_STRUCTURE_CODES,
    row_in_sme_dpf_scope,
    row_matches_dpf_view_filter,
)
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_overwrite_file
from sme_gend.ui.views.edition_sme_view import EditionSmeView


class SmeDpfView(EditionSmeView):
    """
    Édition, recherche et export du personnel DPF.

    Même ergonomie qu'Édition SME, limitée au périmètre DPF (structures DPF, CS-DPF,
    COMGEND avec lien DPF). L'export Excel reprend le gabarit officiel du mois actif.
    """

    def __init__(
        self,
        settings: Settings,
        parent=None,
        *,
        navigate_to_page=None,
    ) -> None:
        super().__init__(
            settings,
            parent,
            navigate_to_page=navigate_to_page,
            view_title="SME DPF — Personnel Direction du Personnel et de la Formation",
            row_scope=row_in_sme_dpf_scope,
            edit_permission="effectifs.sme_dpf",
            settings_app="EditionDPF",
            qsettings_prefix="edition_dpf",
            progress_title="SME DPF",
            default_new_structure="DPF",
            structure_filter_codes=DPF_STRUCTURE_CODES,
            structure_combo_mode="dpf_vue",
            structure_view_filter=row_matches_dpf_view_filter,
        )
        self.setObjectName("smeDpfView")
        self._customize_for_dpf()

    def _customize_for_dpf(self) -> None:
        self._lbl_structure_filter.setText("Vue :")
        self._cmb_structure.setMinimumWidth(180)
        self._cmb_structure.setToolTip(
            "Tous : périmètre DPF complet · DPF : direction · "
            "Contrôle spécial : CS-DPF · COMGEND lié DPF : fonction/subdivision DPF au COMGEND."
        )
        self._lbl_subarme_filter.hide()
        self._cmb_subarme.hide()
        self.setToolTip(
            "Personnel DPF uniquement (structures DPF, CS-DPF, COMGEND lié DPF). "
            "Filtre « Vue » adapté au périmètre DPF (pas la liste de toutes les structures). "
            "Même fonctionnalités qu'Édition SME : recherche, modification inline et actions groupées. "
            "L'export « Classeur complet DPF » génère les tableaux du mois SME affiché "
            "à partir des données en base."
        )

        self._btn_liste.setText("Édition SME (tout)")
        self._btn_liste.setToolTip("Ouvrir l'édition SME sur l'ensemble du personnel")
        try:
            self._btn_liste.clicked.disconnect(self._go_to_liste_generale)
        except TypeError:
            pass
        self._btn_liste.clicked.connect(self._go_to_edition_sme)

        menu = self._btn_export_menu.menu()
        if menu is not None:
            menu.addSeparator()
            menu.addAction(
                "Classeur complet DPF et contrôle spécial…",
                self._export_dpf_workbook,
            )

    def _go_to_edition_sme(self) -> None:
        if self._navigate_to_page is None:
            return
        from sme_gend.ui.navigation_pages import PAGE_EDITION_SME

        self._navigate_to_page(PAGE_EDITION_SME)

    def _update_month_banner(self) -> None:
        super()._update_month_banner()
        base = self._lbl_month.text()
        self._lbl_month.setText(f"{base} · périmètre DPF et contrôle spécial")

    def _export_dpf_workbook(self) -> None:
        from sme_gend.services.auth_guard import require_permission

        if not require_permission(self, "effectifs.export"):
            return


        path, _ = QFileDialog.getSaveFileName(
            self,
            "Enregistrer le SME DPF",
            str(Path.home() / default_export_filename(self._settings)),
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not confirm_overwrite_file(self, Path(path)):
            return

        settings = self._settings

        def work() -> str:
            export_sme_dpf_workbook(settings, Path(path))
            return path

        out = run_with_progress(
            self,
            "SME DPF",
            "Génération des tableaux DPF du mois affiché…",
            work,
        )
        if out:
            QMessageBox.information(
                self,
                "SME DPF",
                f"Fichier enregistré :\n{out}\n\n"
                "Contenu relu depuis MySQL pour le mois affiché.",
            )
