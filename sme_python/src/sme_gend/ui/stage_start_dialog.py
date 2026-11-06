"""Confirmation + fiche optionnelle lors de la mise en stage."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.ui.widgets.date_input import FrenchDateEdit


def _stage_ddf(deb: str, fin: str) -> str | None:
    deb, fin = deb.strip(), fin.strip()
    if deb and fin:
        return f"{deb} — {fin}"
    return deb or fin or None


class StageStartDialog(QDialog):
    """Détails du stage (local / extérieur) avant validation."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        agent_label: str,
        stage_label: str,
        with_fiche: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mettre en stage")
        self.setMinimumWidth(420)

        intro = QLabel(
            f"{agent_label}<br/>"
            f"Position administrative : <b>{stage_label}</b>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)

        self._nature = QLineEdit()
        self._nature.setPlaceholderText("Nature du stage (obligatoire)")
        self._lieu = QLineEdit()
        self._lieu.setPlaceholderText("Lieu (optionnel)")
        self._reference = QLineEdit()
        self._reference.setMaxLength(256)
        self._reference.setPlaceholderText("Facultatif")
        self._session = QLineEdit()
        self._session.setMaxLength(256)
        self._session.setPlaceholderText('Facultatif')
        self._is_internal = 'interne' in stage_label.casefold()
        self._deb = FrenchDateEdit()
        self._fin = FrenchDateEdit()

        form = QFormLayout()
        if with_fiche or self._is_internal:
            hint = QLabel(
                "Nature et date de début obligatoires. Vous pourrez compléter la fiche "
                "depuis le panneau à droite du tableau."
            )
            if not with_fiche:
                hint.setText('Nature obligatoire ; les autres informations du stage interne sont facultatives.')
            hint.setWordWrap(True)
            form.addRow(hint)
            form.addRow("Nature * :", self._nature)
            form.addRow("Lieu :", self._lieu)
            form.addRow("Réf/note (facultatif) :", self._reference)
            if self._is_internal:
                form.addRow('Session (facultatif) :', self._session)
            form.addRow("Date début * :" if with_fiche else 'Date début :', self._deb)
            form.addRow("Date fin :", self._fin)
        else:
            note = QLabel(
                "Aucune fiche satellite pour le stage interne — "
                "seule la position administrative sera modifiée."
            )
            note.setWordWrap(True)
            form.addRow(note)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Confirmer")
        bb.accepted.connect(self._try_accept)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(bb)
        self._with_fiche = with_fiche

    def _try_accept(self) -> None:
        if not self._nature.text().strip():
            QMessageBox.warning(self, 'Nature obligatoire', 'La nature du stage est obligatoire.')
            self._nature.setFocus()
            return
        if self._with_fiche:
            if not self._nature.text().strip():
                QMessageBox.warning(self, "Stage", "La nature du stage est obligatoire.")
                return
            if not self._deb.to_datetime():
                QMessageBox.warning(self, "Stage", "La date de début est obligatoire.")
                return
        self.accept()

    def stage_detail(self) -> StageDetail | None:
        deb = self._deb.text_for_validation().strip()
        fin = self._fin.text_for_validation().strip()
        nature = self._nature.text().strip() or None
        lieu = self._lieu.text().strip() or None
        ddf = _stage_ddf(deb, fin)
        if not any((nature, lieu, ddf, self._reference.text().strip(), self._session.text().strip())):
            return None
        return StageDetail(
            nature_stage=nature,
            lieu_stage=lieu,
            reference_dossier=self._reference.text().strip() or None,
            session=(self._session.text().strip() or None) if self._is_internal else None,
            date_debut_fin=ddf,
        )

    def stage_dates(self) -> tuple[str | None, str | None]:
        deb = self._deb.text_for_validation().strip() or None
        fin = self._fin.text_for_validation().strip() or None
        return deb, fin
