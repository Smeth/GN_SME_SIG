"""Bandeau qualité organigramme + assistant de nettoyage."""

from __future__ import annotations

from sme_gend.ui.reference_task import run_reference_task

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.structure_organigram_service import (
    OrganigramQualitySummary,
    StructureOrganigramService,
)
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.ui.responsive_panels import WrapLayout


class OrganigramQualityBanner(QFrame):
    def __init__(self, referentiel: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("organigramQualityBanner")
        self._ref = referentiel
        self._svc = StructureOrganigramService(referentiel.settings)
        self._summary: OrganigramQualitySummary | None = None

        self._lbl = QLabel("—")
        self._lbl.setWordWrap(True)
        self._lbl.setTextFormat(Qt.TextFormat.RichText)
        self._btn_harmonize = QPushButton("Harmonisation manuelle…")
        self._btn_harmonize.setToolTip(
            "Ouvre l’onglet Harmonisation (fusion SubArme et structures, toutes archives)."
        )
        self._btn_harmonize.clicked.connect(self._open_harmonization)
        self._btn_cleanup = QPushButton("Nettoyer les subdivisions…")
        self._btn_cleanup.setToolTip(
            "Backfill RSA depuis la carrière, rattache les subdivisions « données seules », "
            "puis synchronise la mise en page liste des présents."
        )
        self._btn_cleanup.clicked.connect(self._run_cleanup)

        lay = WrapLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.addWidget(self._lbl)
        lay.addWidget(self._btn_harmonize)
        lay.addWidget(self._btn_cleanup)

    def mark_stale(self):
        self._summary = None
        self._lbl.setText("Qualité des données : non actualisée. Rechargez les structures.")
        self._btn_cleanup.setEnabled(False)

    def refresh(self, summary: OrganigramQualitySummary | None = None) -> None:
        if self._svc.settings != self._ref.settings:
            self._svc.rebind_settings(self._ref.settings)
            self._summary = None
            summary = None
        if summary is None:
            try:
                summary = run_reference_task(self,"Référentiel",lambda: self._svc.quality_summary())
            except Exception:
                summary = None
        self._summary = summary
        if summary is None:
            self._lbl.setText("Qualité des données : indisponible (MySQL).")
            self._btn_cleanup.setEnabled(False)
            return
        self._btn_cleanup.setEnabled(summary.subdiv_data_only_count > 0)
        warn = ""
        if summary.subdiv_data_only_count:
            warn = (
                f"<b>{summary.subdiv_data_only_count}</b> subdivision(s) « données seules » "
                f"(orange) à rattacher au référentiel. "
            )
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        period = resolve_liste_view_mois(self._ref.settings) or "courante"
        self._lbl.setText(
            f"{warn}"
            f"<b>{summary.structure_count}</b> structure(s) · "
            f"<b>{summary.subdiv_ref_count}</b> subdivision(s) référentiel · "
            f"<b>{summary.total_agents}</b> agent(s) (période consultée : {period})."
        )

    def _open_harmonization(self) -> None:
        host = self.parent()
        while host is not None and not hasattr(host, "open_harmonization"):
            host = host.parent()
        if host is not None and hasattr(host, "open_harmonization"):
            host.open_harmonization()
            return
        QMessageBox.information(
            self,
            "Harmonisation",
            "Utilisez « Maintenance ▾ » → Harmonisation des archives "
            "dans Structures & subdivisions.",
        )

    def _run_cleanup(self) -> None:
        if self._svc.settings != self._ref.settings:
            self.refresh()
            QMessageBox.warning(self,"Contexte modifié","Les données ont été actualisées pour la connexion courante. Vérifiez-les avant de relancer le nettoyage.")
            return
        if not confirm_yes_no(
            self,
            "Nettoyer l'organigramme",
            "Cette opération va :\n"
            "1. Créer les RSA manquants depuis la carrière\n"
            "2. Rattacher les subdivisions « données seules »\n"
            "3. Synchroniser la mise en page liste des présents\n\n"
            "Continuer ?",
            default_no=True,
        ):
            return
        try:
            n_bf, n_att, (new_s, new_sub) = run_reference_task(self,"Référentiel",lambda: self._svc.run_cleanup_wizard())
        except Exception as e:
            QMessageBox.critical(self, "Nettoyage", str(e))
            return
        self.refresh()
        QMessageBox.information(
            self,
            "Nettoyage terminé",
            f"RSA créés (backfill) : {n_bf}\n"
            f"Subdivisions rattachées : {n_att}\n"
            f"Structures ajoutées à la mise en page : {len(new_s)}\n"
            f"Subdivisions ajoutées à la mise en page : {len(new_sub)}",
        )
        host = self.parent()
        while host is not None and not hasattr(host, "reload"):
            host = host.parent()
        if host is not None and hasattr(host, "reload"):
            host.reload()
