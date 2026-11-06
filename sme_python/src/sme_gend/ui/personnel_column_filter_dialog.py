"""Dialogue filtre sur en-tête de colonne (style Excel)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from sme_gend.ui.personnel_advanced_filter import (
    OPS_BY_KIND,
    AdvancedFilterRule,
    column_kind,
    default_operator_for,
    is_filterable_column,
)


class PersonnelColumnFilterDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        column_key: str,
        column_label: str,
        existing: AdvancedFilterRule | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Filtre — {column_label}")
        self.setMinimumWidth(360)
        self._column_key = column_key
        self._result: AdvancedFilterRule | None = existing

        self._op = QComboBox()
        kind = column_kind(column_key)
        for op in OPS_BY_KIND[kind]:
            self._op.addItem(op)
        if existing:
            idx = self._op.findText(existing.operator)
            if idx >= 0:
                self._op.setCurrentIndex(idx)
        else:
            idx = self._op.findText(default_operator_for(column_key))
            if idx >= 0:
                self._op.setCurrentIndex(idx)

        self._value = QLineEdit()
        if existing and existing.value:
            self._value.setText(existing.value)
        self._value.setPlaceholderText(
            "jj/mm/aaaa pour une date ; laisser vide si « est vide »"
        )

        self._op.currentTextChanged.connect(self._sync_value)

        hint = QLabel(
            "Ce filtre s'applique en plus des autres (ET entre colonnes). "
            "Utilisez « Effacer » pour retirer le filtre de cette colonne."
        )
        hint.setWordWrap(True)
        hint.setObjectName("settingsPathsHint")

        form = QFormLayout()
        form.addRow("Colonne :", QLabel(column_label))
        form.addRow("Condition :", self._op)
        form.addRow("Valeur :", self._value)

        btn_clear = QPushButton("Effacer le filtre de cette colonne")
        btn_clear.clicked.connect(self._on_clear)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btn_clear)
        lay.addWidget(hint)
        lay.addWidget(buttons)
        self._sync_value(self._op.currentText())

    def _sync_value(self, op: str) -> None:
        need = op not in ("est vide", "n'est pas vide")
        self._value.setEnabled(need)
        if not need:
            self._value.clear()

    def _on_clear(self) -> None:
        self._result = None
        self.accept()

    def _on_ok(self) -> None:
        op = self._op.currentText()
        val = self._value.text().strip()
        if op not in ("est vide", "n'est pas vide") and not val:
            self._result = None
        else:
            self._result = AdvancedFilterRule(
                column_key=self._column_key,
                operator=op,
                value=val,
            )
        self.accept()

    @staticmethod
    def run(
        parent,
        *,
        column_key: str,
        column_label: str,
        existing: AdvancedFilterRule | None = None,
    ) -> AdvancedFilterRule | None | bool:
        """
        Retourne la règle, ``None`` si effacée, ``False`` si annulé.
        """
        if not is_filterable_column(column_key):
            return False
        dlg = PersonnelColumnFilterDialog(
            parent,
            column_key=column_key,
            column_label=column_label,
            existing=existing,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        return dlg._result
