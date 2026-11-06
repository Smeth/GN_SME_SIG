"""Boîtes de dialogue de confirmation pour limiter les erreurs de manipulation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QMessageBox, QWidget


def confirm_yes_no(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    default_no: bool = True,
    rich_text: bool = False,
) -> bool:
    """Retourne True si l'utilisateur choisit Oui.

    Les libellés Oui/Non sont fixés explicitement : sans traductions Qt (qtbase_fr.qm),
    QMessageBox afficherait « Yes » / « No ».
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    if rich_text:
        box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(text)
    box.setStandardButtons(
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    box.setDefaultButton(
        QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes,
    )
    box.setButtonText(QMessageBox.StandardButton.Yes, "Oui")
    box.setButtonText(QMessageBox.StandardButton.No, "Non")
    return box.exec() == QMessageBox.StandardButton.Yes


def confirm_destructive_with_ack(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    ack_label: str,
    confirm_label: str,
) -> bool:
    """Confirmation forte pour une action irréversible unique.

    Le bouton de confirmation (stylé « destructif », rouge) reste désactivé tant que
    l'utilisateur n'a pas coché la case d'acquittement — remplace l'enchaînement de
    plusieurs boîtes Oui/Non banalisées par une seule confirmation qui exige un geste
    explicite.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    checkbox = QCheckBox(ack_label)
    box.setCheckBox(checkbox)
    btn_confirm = box.addButton(confirm_label, QMessageBox.ButtonRole.AcceptRole)
    btn_confirm.setProperty("destructive", True)
    btn_confirm.setEnabled(False)
    btn_cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(btn_cancel)
    checkbox.toggled.connect(btn_confirm.setEnabled)
    box.exec()
    return box.clickedButton() is btn_confirm


def confirm_overwrite_file(parent: QWidget | None, path: str | Path) -> bool:
    """Si le fichier existe, demande avant d'écraser. Sinon True."""
    p = Path(path)
    if not p.is_file():
        return True
    return confirm_yes_no(
        parent,
        "Fichier existant",
        f"Ce fichier existe déjà :\n{p}\n\nSouhaitez-vous le remplacer ?",
        default_no=True,
    )


def show_scrollable_html_message(
    parent: QWidget | None,
    title: str,
    html_body: str,
    *,
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    max_height: int = 420,
    min_width: int = 520,
) -> None:
    """Affiche un rapport HTML long sans dépasser l'écran (contenu défilant + OK visible)."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QScrollArea,
        QSizePolicy,
        QStyle,
        QVBoxLayout,
    )

    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    dlg.setMinimumWidth(min_width)

    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        dlg.setMaximumHeight(max(360, int(avail.height() * 0.85)))
        dlg.setMaximumWidth(max(520, int(avail.width() * 0.9)))

    root = QVBoxLayout(dlg)
    root.setContentsMargins(16, 14, 16, 12)
    root.setSpacing(12)

    header = QHBoxLayout()
    header.setSpacing(12)
    icon_lbl = QLabel()
    style = dlg.style()
    if style is not None:
        std = {
            QMessageBox.Icon.Information: QStyle.StandardPixmap.SP_MessageBoxInformation,
            QMessageBox.Icon.Warning: QStyle.StandardPixmap.SP_MessageBoxWarning,
            QMessageBox.Icon.Critical: QStyle.StandardPixmap.SP_MessageBoxCritical,
            QMessageBox.Icon.Question: QStyle.StandardPixmap.SP_MessageBoxQuestion,
        }.get(icon, QStyle.StandardPixmap.SP_MessageBoxInformation)
        icon_lbl.setPixmap(style.standardIcon(std).pixmap(32, 32))
    header.addWidget(icon_lbl, alignment=Qt.AlignmentFlag.AlignTop)

    body = QLabel(html_body)
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setWordWrap(True)
    body.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.LinksAccessibleByMouse
    )
    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(body)
    scroll.setMinimumHeight(160)
    scroll.setMaximumHeight(max_height)
    scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    header.addWidget(scroll, stretch=1)
    root.addLayout(header, stretch=1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
    if ok_btn is not None:
        ok_btn.setText("OK")
    buttons.accepted.connect(dlg.accept)
    root.addWidget(buttons)

    dlg.adjustSize()
    if screen is not None:
        geo = dlg.frameGeometry()
        geo.moveCenter(screen.availableGeometry().center())
        dlg.move(geo.topLeft())
    dlg.exec()
