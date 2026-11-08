"""Sélection mois / année pour un import Access → MySQL."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sme_gend.config.sme_context import load_sme_context
from sme_gend.services.sme_month_closure import extract_yyyy_mm
from sme_gend.ui.views.sme_workflow_panel import _widgets_to_ym, _ym_to_widgets

_DIALOG_WIDTH = 380
_MONTH_COMBO_WIDTH = 148
_YEAR_SPIN_WIDTH = 80


def pick_import_mois(parent=None, *, default_ym: str | None = None,
                     title: str = "Mois des données à importer (Access)",
                     description: str | None = None) -> str | None:
    """
    Dialogue modal : retourne ``YYYY-MM`` ou ``None`` si annulé.
    """
    ctx = load_sme_context()
    hint = (default_ym or ctx.mois_actif or "").strip()

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setSizeGripEnabled(False)
    dlg.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    content = QWidget()
    content.setFixedWidth(_DIALOG_WIDTH)
    lay = QVBoxLayout(content)
    lay.setContentsMargins(16, 16, 16, 12)
    lay.setSpacing(10)

    info = QLabel(description or (
        "Indiquez le <b>mois et l'année</b> de cette archive Access "
        "(état des agents à cette date).<br><br>"
        "Chaque import enregistre une <b>situation mensuelle des agents</b> dans MySQL pour ce mois. "
        "Répétez pour chaque archive mensuelle."
    ))
    info.setWordWrap(True)
    info.setTextFormat(Qt.TextFormat.RichText)
    info.setMaximumWidth(_DIALOG_WIDTH - 32)
    lay.addWidget(info)

    row_m = QComboBox()
    row_m.setFixedWidth(_MONTH_COMBO_WIDTH)
    for i in range(1, 13):
        row_m.addItem(
            [
                "Janvier",
                "Février",
                "Mars",
                "Avril",
                "Mai",
                "Juin",
                "Juillet",
                "Août",
                "Septembre",
                "Octobre",
                "Novembre",
                "Décembre",
            ][i - 1],
            userData=i,
        )
    row_y = QSpinBox()
    row_y.setRange(2000, 2100)
    row_y.setFixedWidth(_YEAR_SPIN_WIDTH)
    _ym_to_widgets(hint, row_m, row_y)

    fields = QHBoxLayout()
    fields.setSpacing(8)
    fields.addWidget(QLabel("Mois :"))
    fields.addWidget(row_m)
    fields.addSpacing(12)
    fields.addWidget(QLabel("Année :"))
    fields.addWidget(row_y)
    fields.addStretch()
    lay.addLayout(fields)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    btn_row = QHBoxLayout()
    btn_row.addStretch()
    btn_row.addWidget(buttons)
    lay.addLayout(btn_row)

    outer = QVBoxLayout(dlg)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(content, alignment=Qt.AlignmentFlag.AlignHCenter)

    dlg.adjustSize()
    dlg.setFixedSize(dlg.sizeHint())

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    ym = _widgets_to_ym(row_m, row_y)
    if not ym or extract_yyyy_mm(ym) != ym:
        return None
    return ym


@dataclass(slots=True)
class ImportOptions:
    """Résultat de ``pick_import_options`` : mois + mode d'import choisis."""

    mois: str
    archive_and_close: bool
    mois_suivant: str | None
    preserve_manual: bool = True


def pick_import_options(parent=None, *, default_ym: str | None = None, allow_legacy_cycle: bool = True) -> ImportOptions | None:
    """
    Dialogue modal : mois de l'import + option d'archivage/clôture + mois suivant.

    Coché (par défaut) « Archiver le fichier et clôturer ce mois » : copie le fichier
    dans ``sme_archives/``, marque le mois comme Fermé, permet d'activer le mois suivant.
    Décoché : import MySQL seul, sans copie ni clôture (import ponctuel/complément).

    Retourne ``None`` si annulé.
    """
    from sme_gend.ui.views.sme_workflow_panel import (
        _next_yyyy_mm,
        _sme_month_combo,
        _sme_year_spin,
    )

    ctx = load_sme_context()
    hint = (default_ym or ctx.mois_actif or "").strip()

    dlg = QDialog(parent)
    dlg.setWindowTitle("Importer les données d’un mois — options")
    dlg.setModal(True)
    dlg.setMinimumWidth(440)
    lay = QVBoxLayout(dlg)

    info = QLabel(
        "Indiquez le <b>mois et l'année</b> de cette archive Access (état des agents à cette date)."
    )
    info.setWordWrap(True)
    info.setTextFormat(Qt.TextFormat.RichText)
    lay.addWidget(info)

    g_mois = QGroupBox("Mois de l'import")
    ml = QVBoxLayout(g_mois)
    row_c = QHBoxLayout()
    row_c.addWidget(QLabel("Mois :"))
    cb_c = _sme_month_combo(for_dialog=True)
    cb_c.setObjectName("importMonth")
    sp_c = _sme_year_spin(for_dialog=True)
    sp_c.setObjectName("importYear")
    _ym_to_widgets(hint, cb_c, sp_c)
    row_c.addWidget(cb_c)
    row_c.addWidget(QLabel("Année :"))
    row_c.addWidget(sp_c)
    row_c.addStretch()
    ml.addLayout(row_c)
    lay.addWidget(g_mois)

    g_replace = QGroupBox("Données déjà présentes dans ce mois")
    replace_lay = QVBoxLayout(g_replace)
    replacement_mode = QComboBox()
    replacement_mode.setObjectName("importReplacementMode")
    replacement_mode.addItem("Conserver les saisies et reprises", True)
    replacement_mode.addItem("Remplacer entièrement le contenu mensuel", False)
    from sme_gend.services.auth_guard import session_has_admin_role
    if not session_has_admin_role():
        replacement_mode.model().item(1).setEnabled(False)
    replacement_mode.setItemData(1, "Réservé aux administrateurs", Qt.ItemDataRole.ToolTipRole)
    replace_lay.addWidget(replacement_mode)
    replacement_hint = QLabel(
        "La liste des agents sera alignée sur le fichier dans les deux modes. "
        "Le premier conserve les activités saisies ou reprises sans import. "
        "Le second les supprime aussi, ainsi que les tâches et acquittements d’alertes du mois. "
        "Un aperçu chiffré précédera la confirmation. Les identités et les événements "
        "durables encore attestés ailleurs restent protégés."
    )
    replacement_hint.setWordWrap(True)
    replacement_hint.setMinimumHeight(replacement_hint.fontMetrics().lineSpacing() * 7)
    replace_lay.addWidget(replacement_hint)
    lay.addWidget(g_replace)

    chk_archive = QCheckBox("Archiver le fichier et clôturer ce mois")
    chk_archive.setObjectName("archiveAndClose")
    chk_archive.setToolTip(
        "Coché : copie le fichier dans sme_archives/, marque le mois comme Fermé "
        "et permet d'activer le mois suivant.\n"
        "Décoché : import MySQL seul, sans copie ni clôture (import ponctuel/complément)."
    )
    chk_archive.setChecked(True)
    lay.addWidget(chk_archive)

    chk_suivant = QCheckBox("Activer le mois suivant après import")
    chk_suivant.setObjectName("activateNextMonth")
    chk_suivant.setChecked(True)
    lay.addWidget(chk_suivant)

    g_suivant = QGroupBox("Mois suivant à activer")
    g_suivant.setObjectName("nextMonthGroup")
    sl2 = QVBoxLayout(g_suivant)
    row_s = QHBoxLayout()
    row_s.addWidget(QLabel("Mois :"))
    cb_s = _sme_month_combo(for_dialog=True)
    cb_s.setObjectName("nextMonth")
    sp_s = _sme_year_spin(for_dialog=True)
    sp_s.setObjectName("nextYear")
    _ym_to_widgets("", cb_s, sp_s)
    row_s.addWidget(cb_s)
    row_s.addWidget(QLabel("Année :"))
    row_s.addWidget(sp_s)
    row_s.addStretch()
    sl2.addLayout(row_s)

    btn_auto = QPushButton("Remplir automatiquement (mois + 1)")

    def _fill_suivant() -> None:
        nn = _next_yyyy_mm(_widgets_to_ym(cb_c, sp_c))
        if nn:
            _ym_to_widgets(nn, cb_s, sp_s)

    btn_auto.clicked.connect(_fill_suivant)
    sl2.addWidget(btn_auto)
    lay.addWidget(g_suivant)

    def _toggle_suivant(checked: bool) -> None:
        g_suivant.setEnabled(checked and chk_archive.isChecked())

    def _toggle_archive(checked: bool) -> None:
        if not checked:
            chk_suivant.setChecked(False)
        chk_suivant.setEnabled(checked)
        _toggle_suivant(chk_suivant.isChecked())

    chk_archive.toggled.connect(_toggle_archive)
    chk_suivant.toggled.connect(_toggle_suivant)
    _fill_suivant()
    cb_c.currentIndexChanged.connect(lambda _index: _fill_suivant())
    sp_c.valueChanged.connect(lambda _value: _fill_suivant())
    _toggle_archive(chk_archive.isChecked())
    if not allow_legacy_cycle:
        chk_archive.setChecked(False)
        chk_archive.setEnabled(False)
        info.setText("Cycle documentaire actif : import historique uniquement. Le mois de travail et les statuts de clôture sont conservés.")

    bb = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    bb.button(QDialogButtonBox.StandardButton.Ok).setText("Continuer")
    bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
    def _accept_options() -> None:
        if chk_archive.isChecked() and chk_suivant.isChecked():
            if _widgets_to_ym(cb_s, sp_s) <= _widgets_to_ym(cb_c, sp_c):
                QMessageBox.warning(dlg, "Mois suivant invalide",
                                    "Le mois à activer doit être postérieur au mois importé.")
                return
        dlg.accept()

    bb.accepted.connect(_accept_options)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)

    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None

    mois = _widgets_to_ym(cb_c, sp_c)
    if not mois or extract_yyyy_mm(mois) != mois:
        return None

    archive_and_close = chk_archive.isChecked()
    mois_suivant: str | None = None
    if archive_and_close and chk_suivant.isChecked():
        ms = _widgets_to_ym(cb_s, sp_s)
        if extract_yyyy_mm(ms) == ms:
            mois_suivant = ms

    return ImportOptions(mois=mois, archive_and_close=archive_and_close, mois_suivant=mois_suivant,
                         preserve_manual=bool(replacement_mode.currentData()))
