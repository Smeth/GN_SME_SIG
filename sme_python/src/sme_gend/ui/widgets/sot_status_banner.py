"""Bandeau de contrôle des fiches — valeurs différentes du dossier et correction."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from sme_gend.config.settings import Settings
from sme_gend.services.sot_status_service import SotStatusSummary


class SotStatusBanner(QFrame):
    """Signale les fiches différentes du dossier et propose la correction."""

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        on_resync_done: Callable[[], None] | None = None,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("sotStatusBanner")
        self._settings = settings
        self._on_resync_done = on_resync_done
        self._compact = compact
        self._summary: SotStatusSummary | None = None

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextFormat(Qt.TextFormat.RichText)

        self._btn_resync = QPushButton("Synchroniser depuis le dossier…")
        self._btn_resync.setToolTip(
            "Recopie le grade, le diplôme et l'affectation du dossier de l'agent "
            "sur la fiche du mois."
        )
        self._btn_resync.clicked.connect(self._on_resync)

        self._btn_reconcile = QPushButton("Compléter les référentiels…")
        self._btn_reconcile.setToolTip(
            "Rattache les libellés des fiches aux référentiels "
            "(grade, structure, position administrative, fonction)."
        )
        self._btn_reconcile.clicked.connect(self._on_reconcile_fk)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)
        row.addWidget(self._label, stretch=1)
        if not compact:
            row.addWidget(self._btn_reconcile, alignment=Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._btn_resync, alignment=Qt.AlignmentFlag.AlignVCenter)
        self.hide()

    def apply_settings(self, settings: Settings) -> None:
        self._settings = settings

    def update_summary(self, summary: SotStatusSummary | None) -> None:
        self._summary = summary
        if summary is None or not summary.has_issues:
            self.hide()
            return
        parts: list[str] = []
        n_gap = len(summary.gap_ids)
        n_fk = len(summary.fk_unresolved_ids)
        if n_gap:
            parts.append(
                f"<b>{n_gap}</b> fiche(s) dont le grade ou le diplôme "
                f"diffère du dossier de l'agent"
            )
        if summary.ecarts_affectation:
            parts.append(
                f"<b>{summary.ecarts_affectation}</b> fiche(s) dont l'affectation "
                f"diffère du dossier"
            )
        if n_fk:
            detail = []
            if summary.fk_grade:
                detail.append(f"grade {summary.fk_grade}")
            if summary.fk_structure:
                detail.append(f"structure {summary.fk_structure}")
            if summary.fk_padmin:
                detail.append(f"position admin. {summary.fk_padmin}")
            if summary.fk_fonction:
                detail.append(f"fonction {summary.fk_fonction}")
            if summary.fk_subdiv:
                detail.append(f"subdivision {summary.fk_subdiv}")
            extra = f" ({', '.join(detail)})" if detail else ""
            parts.append(
                f"<b>{n_fk}</b> ligne(s) sans correspondance dans les "
                f"référentiels{extra}"
            )
        mois = (summary.mois or "").strip()
        prefix = f"Mois <b>{mois}</b> — " if mois else ""
        self._label.setText(prefix + " · ".join(parts) + ".")
        self._btn_resync.setVisible(n_gap > 0 or summary.ecarts_affectation > 0)
        self._btn_reconcile.setVisible(n_fk > 0 and not self._compact)
        self._apply_style(warning=True)
        self.show()

    def _apply_style(self, *, warning: bool) -> None:
        from sme_gend.ui.theme import is_dark, theme_palette

        pal = theme_palette()
        if warning:
            bg = "#3d3520" if is_dark() else "#fff3e0"
            border = "#5c4a2a" if is_dark() else "#ffcc80"
            fg = pal["warning"]
        else:
            bg = pal["bg_elevated"]
            border = pal["border"]
            fg = pal["text_muted"]
        self.setStyleSheet(
            f"QFrame#sotStatusBanner {{ background: {bg}; color: {fg}; "
            f"border-radius: 6px; border: 1px solid {border}; }}"
        )

    def _on_resync(self) -> None:
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        from sme_gend.ui.bg_task import run_with_progress
        from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch

        mois = (
            (self._summary.mois if self._summary else None)
            or resolve_liste_view_mois(self._settings)
            or ""
        ).strip() or None

        def work():
            return project_carriere_from_dossier_batch(
                self._settings,
                mois=mois,
                only_est_actuelle=not bool(mois),
            )

        label = mois or "mois en cours"
        result = run_with_progress(
            self,
            "Synchronisation des fiches",
            f"Reprise des valeurs du dossier vers la fiche ({label})…",
            work,
        )
        if result is None:
            return
        if self._on_resync_done:
            self._on_resync_done()

    def _on_reconcile_fk(self) -> None:
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        from sme_gend.ui.bg_task import run_with_progress
        from sme_gend.services.sot_maintenance import resync_fk_referentiels

        mois = (
            (self._summary.mois if self._summary else None)
            or resolve_liste_view_mois(self._settings)
            or ""
        ).strip() or None

        def work():
            return resync_fk_referentiels(self._settings, mois=mois)

        result = run_with_progress(
            self,
            "Complément des référentiels",
            "Rattachement des libellés aux référentiels…",
            work,
        )
        if result is None:
            return
        if self._on_resync_done:
            self._on_resync_done()
