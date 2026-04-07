"""Vues référentiels (lecture seule)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.ref_table_model import RefTableModel


class _RefBase(QWidget):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._model = RefTableModel([], [])
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setAlternatingRowColors(True)

        self._btn = QPushButton("Actualiser")
        self._btn.clicked.connect(self.reload)

        self._hint = QLabel()
        self._hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(self._hint)
        lay.addWidget(self._btn)
        lay.addWidget(self._table)

    def reload(self) -> None:
        raise NotImplementedError


class StructureSubdivisionView(_RefBase):
    """Chaque structure avec les subdivisions d'arme rencontrées dans ListeGenerale."""

    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._hint.setText(
            "<b>Structures ↔ subdivisions d'arme</b> — couples distincts "
            "(Structure, SubArme) issus de la liste générale. Lecture seule."
        )
        self.reload()

    def reload(self) -> None:
        try:
            pairs = self._service.structure_subdivision_pairs()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [[a, b] for a, b in pairs]
        self._model.set_data(["Structure (code)", "Subdivision d'arme"], data)


class StructuresView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._hint.setText(
            "<b>Structures</b> (TabStructure) — lecture seule. "
            "L’édition sera ajoutée dans une prochaine version."
        )
        self.reload()

    def reload(self) -> None:
        try:
            rows = self._service.structures()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [[r.code, r.designation, str(r.ordre_tri if r.ordre_tri is not None else "")] for r in rows]
        self._model.set_data(["Code", "Désignation", "Ordre tri"], data)


class SubdivisionsView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._hint.setText(
            "<b>Subdivisions</b> — hiérarchie Secun → Secdeux → Sectrois (jointure). "
            "Lecture seule."
        )
        self.reload()

    def reload(self) -> None:
        try:
            flat = self._service.subdivisions_flat()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.code_u, r.design_u, r.code_d, r.design_d, r.code_t, r.design_t]
            for r in flat
        ]
        self._model.set_data(
            ["Code U", "Niveau 1", "Code D", "Niveau 2", "Code T", "Niveau 3"],
            data,
        )


class GradesView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._hint.setText("<b>Grades</b> (TabGrade) — ordre de préséance. Lecture seule.")
        self.reload()

    def reload(self) -> None:
        try:
            rows = self._service.grades()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [
            [r.code, r.libelle, str(r.pref_ord if r.pref_ord is not None else ""), str(r.effectif_g or "")]
            for r in rows
        ]
        self._model.set_data(["Code", "Grade", "Préf. ordre", "Effectif"], data)


class FonctionsView(_RefBase):
    def __init__(self, service: ReferentielService, parent=None) -> None:
        super().__init__(service, parent)
        self._hint.setText(
            "<b>Fonctions</b> — valeurs distinctes dans ListeGenerale.Fonction "
            "(pas de table dédiée dans l’extrait)."
        )
        self.reload()

    def reload(self) -> None:
        try:
            fns = self._service.fonctions_distinctes()
        except Exception as e:
            QMessageBox.critical(self, "Erreur", str(e))
            return
        data = [[f] for f in fns]
        self._model.set_data(["Fonction"], data)
