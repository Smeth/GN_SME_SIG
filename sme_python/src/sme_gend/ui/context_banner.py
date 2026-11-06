"""Bandeau de contexte (période, source, verrouillage saisie)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QToolButton, QSizePolicy

from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.sme_editing_policy import evaluate_editing_state
from sme_gend.services.sme_workspace_service import SmeWorkspaceService
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.ui.theme import theme_palette


def _format_mois_fr(mois: str) -> str:
    if not mois:
        return "—"
    try:
        y, m = mois.split("-")
        mois_fr = (
            "janvier", "février", "mars", "avril", "mai", "juin",
            "juillet", "août", "septembre", "octobre", "novembre", "décembre",
        )
        mi = int(m)
        if 1 <= mi <= 12:
            return f"{mois_fr[mi - 1].capitalize()} {y}"
    except (ValueError, IndexError):
        pass
    return mois


class ContextBanner(QFrame):
    """Affiche mois actif, source données et état de saisie."""

    def __init__(self, settings: Settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setObjectName("contextBanner")
        primary = QHBoxLayout(self)
        primary.setContentsMargins(12, 4, 12, 4)
        primary.setSpacing(12)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        self._btn_nav_back = QToolButton()
        self._btn_nav_back.setObjectName("contextBannerNavBtn")
        self._btn_nav_back.setText("←")
        self._btn_nav_back.setToolTip("Page précédente (Alt+←)")
        self._btn_nav_back.setAutoRaise(True)
        self._btn_nav_back.setEnabled(False)
        self._btn_nav_forward = QToolButton()
        self._btn_nav_forward.setObjectName("contextBannerNavBtn")
        self._btn_nav_forward.setText("→")
        self._btn_nav_forward.setToolTip("Page suivante (Alt+→)")
        self._btn_nav_forward.setAutoRaise(True)
        self._btn_nav_forward.setEnabled(False)
        nav_row.addWidget(self._btn_nav_back)
        nav_row.addWidget(self._btn_nav_forward)
        primary.addLayout(nav_row)

        self._lbl_mois = QLabel()
        self._lbl_mois.setObjectName("contextBannerMois")

        self._lbl_source = QLabel()
        self._lbl_source.setObjectName("contextBannerSource")

        self._lbl_lock = QLabel()
        self._lbl_lock.setObjectName("contextBannerLock")

        self._lbl_mysql = QLabel()
        self._lbl_mysql.setObjectName("contextBannerMysql")
        self._lbl_mysql.setCursor(Qt.CursorShape.PointingHandCursor)
        self._lbl_mysql.setToolTip("Cliquer pour ouvrir la configuration MySQL")
        self._lbl_mysql.mousePressEvent = self._on_mysql_label_click  # type: ignore[method-assign]

        self._lbl_hint = QLabel()
        self._lbl_hint.setObjectName("contextBannerHint")
        self._lbl_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for label in (self._lbl_mois, self._lbl_source, self._lbl_mysql, self._lbl_lock, self._lbl_hint):
            label.setWordWrap(False)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        primary.addWidget(self._lbl_mois, 2)
        primary.addWidget(self._lbl_source, 2)
        primary.addWidget(self._lbl_mysql, 2)
        primary.addWidget(self._lbl_lock, 2)
        primary.addWidget(self._lbl_hint, 1)
        self.set_filter_available(False)
        self._configure_mysql_callback = None
        QTimer.singleShot(0, self.refresh)

    def set_filter_available(self, available: bool) -> None:
        self._lbl_hint.setText("F5 · Ctrl+K" + (" · Ctrl+F filtre" if available else ""))
        self._lbl_hint.setToolTip(
            "Alt+← : page précédente\nAlt+→ : page suivante\nF5 : actualiser\nCtrl+K : rechercher un personnel"
            + ("\nCtrl+F : rechercher dans cette page" if available else ""))

    def set_configure_mysql_callback(self, callback) -> None:
        self._configure_mysql_callback = callback

    def set_page_nav_handlers(
        self,
        back: Callable[[], None] | None,
        forward: Callable[[], None] | None,
    ) -> None:
        if back is not None:
            self._btn_nav_back.clicked.connect(back)
        if forward is not None:
            self._btn_nav_forward.clicked.connect(forward)

    def set_page_nav_enabled(self, *, back: bool, forward: bool) -> None:
        self._btn_nav_back.setEnabled(back)
        self._btn_nav_forward.setEnabled(forward)
        self._btn_nav_back.setToolTip(
            "Page précédente (Alt+←)" if back else "Aucune page précédente"
        )
        self._btn_nav_forward.setToolTip(
            "Page suivante (Alt+→)" if forward else "Aucune page suivante"
        )

    def _on_mysql_label_click(self, event) -> None:
        from PySide6.QtWidgets import QLabel

        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._configure_mysql_callback is not None
        ):
            self._configure_mysql_callback()
            return
        QLabel.mousePressEvent(self._lbl_mysql, event)

    def set_mysql_status(self, online: bool | None, detail: str = "") -> None:
        """``None`` = vérification en cours."""
        pal = theme_palette()
        if online is None:
            self._lbl_mysql.setText(
                f"<span style='color:{pal.get('text_dim', '#666')};'>MySQL : vérification…</span>"
            )
        elif online:
            short = (detail or "connecté").strip()
            self._lbl_mysql.setText(
                f"<span style='color:{pal.get('success', '#2e7d32')};'>"
                f"<b>MySQL OK</b></span>"
            )
            self._lbl_mysql.setToolTip(f"{short}\nCliquer pour ouvrir la configuration MySQL")
        else:
            self._lbl_mysql.setText(
                f"<span style='color:{pal.get('error', '#c0392b')};'>"
                f"<b>MySQL indisponible</b></span>"
            )
            if detail:
                self._lbl_mysql.setToolTip(detail)
        self._lbl_mysql.setTextFormat(Qt.TextFormat.RichText)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.refresh()

    def refresh(self) -> None:
        from sme_gend.services.sme_editing_policy import evaluate_editing_state, peek_editing_state

        view_mois = resolve_liste_view_mois(self._settings) or ""
        try:
            st = peek_editing_state(self._settings, view_mois=view_mois or None) or (
                evaluate_editing_state(self._settings, view_mois=view_mois or None)
            )
        except Exception:
            st = None
        self.refresh_from_state(st)

    def refresh_from_state(self, st) -> None:
        view_mois = canonical_sme_mois_yyyy_mm(resolve_liste_view_mois(self._settings) or "") or ""
        try:
            snapshot = SmeWorkspaceService(self._settings).load_active_month_snapshot()
            operational = snapshot.active_month
            self._month_snapshot = snapshot
        except Exception:
            self._month_snapshot = None
            ctx = load_sme_context()
            operational = canonical_sme_mois_yyyy_mm(ctx.mois_actif or "") or ""
        pal = theme_palette()
        if view_mois and operational and view_mois != operational:
            self._lbl_mois.setText(
                f"<b>Affichage :</b> {_format_mois_fr(view_mois)} "
            )
        else:
            self._lbl_mois.setText(
                f"<b>Période :</b> {_format_mois_fr(view_mois or operational)}"
            )

        mois = operational
        if view_mois and view_mois != mois:
            self._lbl_source.setText(
                f"<span style='color:{pal.get('warning', '#e65100')};'>"
                f"<b>Archive</b> · actif : {_format_mois_fr(mois)}</span>"
            )
        elif view_mois:
            self._lbl_source.setText(
                f"<span style='color:{pal.get('success', '#2e7d32')};'>"
                f"<b>Source :</b> MySQL · mois affiché</span>"
            )
        else:
            self._lbl_source.setText(
                f"<span style='color:{pal.get('info', '#1565c0')};'>"
                f"<b>Source :</b> mois ouvert</span>"
            )
        if st is None:
            self._lbl_lock.setText(
                f"<span style='color:{pal['text_dim']};'>État SME indisponible</span>"
            )
        elif st.locked:
            self._lbl_lock.setText(
                f"<span style='color:{pal['error']};'><b>Saisie bloquée</b> — "
                f"{st.tabsme_statut or 'Fermé'}</span>"
            )
        elif st.tabsme_statut:
            self._lbl_lock.setText(
                f"<span style='color:{pal['success']};'><b>Saisie OK</b> — {st.tabsme_statut}</span>"
            )
        else:
            self._lbl_lock.setText(
                f"<span style='color:{pal['info']};'>Saisie autorisée</span>"
            )

        for lbl in (self._lbl_mois, self._lbl_source, self._lbl_mysql, self._lbl_lock):
            lbl.setTextFormat(Qt.TextFormat.RichText)
