"""Attribution médaille ou décoration / ordre sur le dossier agent."""

from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
)

from sme_gend.models.distinction_ref import (
    DISTINCTION_KIND_DECORATION,
    DISTINCTION_KIND_MEDAILLE,
    AgentDistinctionRow,
    DistinctionRefRow,
)
from sme_gend.ui.widgets.date_input import FrenchDateEdit


class AgentDistinctionEditDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        medailles: list[DistinctionRefRow],
        decorations: list[DistinctionRefRow],
        existing: AgentDistinctionRow | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Modifier la distinction" if existing else "Ajouter médaille ou décoration"
        )
        self.setMinimumWidth(480)
        self._result: dict | None = None
        self._medailles = medailles
        self._decorations = decorations

        self._kind = QComboBox()
        self._kind.addItem("Médaille", DISTINCTION_KIND_MEDAILLE)
        self._kind.addItem("Décoration / ordre", DISTINCTION_KIND_DECORATION)
        self._kind.currentIndexChanged.connect(self._rebuild_ref_combo)

        self._ref = QComboBox()
        self._date_attr = FrenchDateEdit()
        self._date_jo = FrenchDateEdit()
        self._num_arrete = QLineEdit()
        self._date_arrete = FrenchDateEdit()
        self._citation = QTextEdit()
        self._citation.setMaximumHeight(80)

        if existing:
            idx = self._kind.findData(existing.kind)
            if idx >= 0:
                self._kind.setCurrentIndex(idx)
            self._rebuild_ref_combo()
            ref_id = existing.ref_medaille_id or existing.ref_decoration_id
            if ref_id is not None:
                ri = self._ref.findData(int(ref_id))
                if ri >= 0:
                    self._ref.setCurrentIndex(ri)
            if existing.date_attribution:
                self._date_attr.set_from_text(existing.date_attribution.isoformat())
            if existing.date_publication_journal:
                self._date_jo.set_from_text(existing.date_publication_journal.isoformat())
            self._num_arrete.setText(existing.numero_arrete or "")
            if existing.date_arrete:
                self._date_arrete.set_from_text(existing.date_arrete.isoformat())
            self._citation.setPlainText(existing.citation or "")
        else:
            self._rebuild_ref_combo()

        form = QFormLayout()
        form.addRow("Type *", self._kind)
        form.addRow("Distinction *", self._ref)
        form.addRow("Date d'attribution", self._date_attr)
        form.addRow("Publication (JO)", self._date_jo)
        form.addRow("N° arrêté / décision", self._num_arrete)
        form.addRow("Date arrêté", self._date_arrete)
        form.addRow("Citation / mention", self._citation)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(bb)

    def _rebuild_ref_combo(self) -> None:
        kind = str(self._kind.currentData() or DISTINCTION_KIND_MEDAILLE)
        self._ref.clear()
        pool = self._medailles if kind == DISTINCTION_KIND_MEDAILLE else self._decorations
        for r in pool:
            self._ref.addItem(r.display_label(), int(r.id))
        if self._ref.count() == 0:
            self._ref.addItem("(aucun référentiel — configurez Médailles & décorations)", None)

    def _on_ok(self) -> None:
        kind = str(self._kind.currentData() or DISTINCTION_KIND_MEDAILLE)
        ref_id = self._ref.currentData()
        if ref_id is None:
            QMessageBox.warning(
                self,
                "Distinction",
                "Choisissez une médaille ou une décoration dans le référentiel.",
            )
            return

        def _opt_date(widget: FrenchDateEdit) -> date | None:
            dt = widget.to_datetime()
            return dt.date() if dt else None

        self._result = {
            "kind": kind,
            "ref_medaille_id": int(ref_id) if kind == DISTINCTION_KIND_MEDAILLE else None,
            "ref_decoration_id": int(ref_id) if kind == DISTINCTION_KIND_DECORATION else None,
            "date_attribution": _opt_date(self._date_attr),
            "date_publication_journal": _opt_date(self._date_jo),
            "numero_arrete": self._num_arrete.text().strip() or None,
            "date_arrete": _opt_date(self._date_arrete),
            "citation": self._citation.toPlainText().strip() or None,
        }
        self.accept()

    def values(self) -> dict:
        if self._result is None:
            raise RuntimeError("Dialog not accepted")
        return dict(self._result)
