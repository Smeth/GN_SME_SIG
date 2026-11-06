"""Panneau filtres avancés compact (replié par défaut)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDateEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sme_gend.ui.personnel_advanced_filter import (
    FILTERABLE_COLUMNS,
    OPS_BY_KIND,
    AdvancedFilterRule,
    MatchMode,
    column_kind,
    default_operator_for,
    rule_to_dict,
    rules_from_list,
    _duration_months,
)


class _ElidedLabel(QLabel):
    """Résumé sur une ligne, texte complet disponible au survol."""

    def __init__(self) -> None:
        super().__init__()
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()


class PersonnelAdvancedFilterPanel(QWidget):
    """Barre résumé + zone de saisie repliable (ne monopolise pas l'écran)."""

    rules_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._rules: list[AdvancedFilterRule] = []
        self._body_expanded = False

        self._toggle = QToolButton()
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._toggle.setToolTip(
            "Afficher ou masquer la saisie des règles. "
            "Filtre rapide par colonne : clic droit sur l'en-tête du tableau. "
            "Dates jj/mm/aaaa. Plusieurs règles : toutes doivent être vraies (ET)."
        )
        self._toggle.clicked.connect(self._on_toggle_body)

        self._summary = _ElidedLabel()
        self._summary.setObjectName("personnelAdvancedSummary")

        btn_clear = QPushButton("Effacer")
        btn_clear.setToolTip("Supprimer toutes les règles avancées")
        btn_clear.clicked.connect(self.clear_rules)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(self._toggle)
        bar.addWidget(self._summary, stretch=1)
        bar.addWidget(btn_clear)

        self._col = QComboBox()
        self._col.setMinimumWidth(140)
        self._col.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._col.setMinimumContentsLength(10)
        for key, label, _kind in FILTERABLE_COLUMNS:
            self._col.addItem(label, key)

        self._op = QComboBox()
        self._op.setMinimumWidth(110)
        self._op.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._op.setMinimumContentsLength(8)

        self._value = QLineEdit()
        self._value.setPlaceholderText("Valeur…")
        self._value.setMinimumWidth(140)

        btn_add = QPushButton("Appliquer")
        btn_add.setToolTip(
            "Ajoute ou met à jour la règle pour la colonne choisie (la valeur saisie "
            "reste dans le champ pour enchaîner les critères)."
        )
        btn_add.clicked.connect(self._on_add_rule)

        btn_remove = QPushButton("Retirer")
        btn_remove.setToolTip("Retirer la dernière règle ajoutée")
        btn_remove.clicked.connect(self._on_remove_last)

        builder = QGridLayout()
        builder.setSpacing(8)
        builder.setVerticalSpacing(4)
        builder.addWidget(QLabel("Critère"), 0, 0)
        builder.addWidget(self._col, 0, 1)
        builder.addWidget(QLabel("Condition"), 0, 2)
        builder.addWidget(self._op, 0, 3)
        builder.addWidget(QLabel("Valeur"), 1, 0)
        builder.addWidget(self._value, 1, 1, 1, 3)
        builder.addWidget(btn_add, 0, 4)
        builder.addWidget(btn_remove, 1, 4)
        builder.setColumnStretch(1, 1)
        builder.setColumnStretch(3, 1)

        self._rules_line = _ElidedLabel()
        self._rules_line.setObjectName("personnelAdvancedRulesLine")
        self._rules_line.setVisible(False)

        self._body = QFrame()
        self._body.setObjectName("personnelAdvancedBody")
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(0, 4, 0, 0)
        body_lay.setSpacing(4)
        body_lay.addLayout(builder)
        self._duration_options = QWidget()
        options = QGridLayout(self._duration_options)
        options.setContentsMargins(0, 0, 0, 0)
        self._today = QCheckBox("À la date du jour")
        self._today.setChecked(True)
        self._reference = QDateEdit(QDate.currentDate())
        self._reference.setCalendarPopup(True)
        self._reference.setDisplayFormat("dd/MM/yyyy")
        self._reference.setEnabled(False)
        self._today.toggled.connect(lambda checked: self._reference.setEnabled(not checked))
        options.addWidget(QLabel("Date de référence :"), 0, 0)
        options.addWidget(self._today, 0, 1)
        options.addWidget(self._reference, 1, 0, 1, 2)
        options.setColumnStretch(1, 1)
        body_lay.addWidget(self._duration_options)
        duration_help = (
            "Durée : 2 ou 2 ans ; 2 ans 6 mois ; 6 mois. Entre : 2 ans ; 5 ans "
            "(bornes incluses). Égal à : années ou mois révolus. "
            "Les dates absentes, invalides ou futures sont exclues."
        )
        self._duration_hint = _ElidedLabel()
        self._duration_hint.setText("Durée : 2 ans 6 mois. Entre : 2 ans ; 5 ans. Aide au survol.")
        self._duration_hint.setToolTip(duration_help)
        self._value.setToolTip(duration_help)
        body_lay.addWidget(self._duration_hint)
        body_lay.addWidget(self._rules_line)
        self._body.setVisible(False)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 0)
        root.setSpacing(0)
        root.addLayout(bar)
        root.addWidget(self._body)

        self._col.currentIndexChanged.connect(self._sync_operators)
        self._op.currentIndexChanged.connect(self._sync_value_enabled)
        self._sync_operators()
        self._sync_summary()

    def match_mode(self) -> MatchMode:
        """Toujours ET entre les règles avancées (choix OU retiré de l'interface)."""
        return "and"

    def rules(self) -> list[AdvancedFilterRule]:
        return list(self._rules)

    def export_state(self) -> dict[str, Any]:
        return {
            "match_mode": "and",
            "rules": [rule_to_dict(r) for r in self._rules],
            "expanded": self._body_expanded,
        }

    def load_state(self, state: dict[str, Any] | None) -> None:
        self._rules.clear()
        if not state:
            self._set_body_expanded(False)
            self._sync_summary()
            return
        for rule in rules_from_list(state.get("rules")):
            self._rules.append(rule)
        self._set_body_expanded(bool(state.get("expanded") and self._rules))
        self._sync_summary()

    def clear_rules(self) -> None:
        self._rules.clear()
        self._set_body_expanded(False)
        self._sync_summary()
        self.rules_changed.emit()

    def _on_toggle_body(self) -> None:
        self._set_body_expanded(not self._body_expanded)

    def _set_body_expanded(self, expanded: bool) -> None:
        self._body_expanded = expanded
        self._body.setVisible(expanded)
        self._sync_summary()

    def _sync_summary(self) -> None:
        n = len(self._rules)
        if n == 0:
            self._summary.setText("Aucune règle avancée")
            self._toggle.setText("▼ Avancé" if self._body_expanded else "▶ Avancé")
            self._rules_line.setVisible(False)
            return
        labels = [r.label() for r in self._rules[:4]]
        extra = f" (+{n - 4})" if n > 4 else ""
        if n == 1:
            self._summary.setText(f"1 règle : {labels[0]}")
        else:
            self._summary.setText(f"{n} règles (et) : " + " et ".join(labels) + extra)
        self._toggle.setText("▼ Avancé" if self._body_expanded else "▶ Avancé")
        self._rules_line.setText(" · ".join(r.label() for r in self._rules))
        self._rules_line.setVisible(self._body_expanded and n > 0)

    def _sync_operators(self) -> None:
        key = self._col.currentData()
        if not key:
            return
        kind = column_kind(str(key))
        prev = self._op.currentText()
        self._op.blockSignals(True)
        self._op.clear()
        for op in OPS_BY_KIND[kind]:
            self._op.addItem(op)
        idx = self._op.findText(prev)
        if idx < 0:
            idx = self._op.findText(default_operator_for(str(key)))
        self._op.setCurrentIndex(max(0, idx))
        self._op.blockSignals(False)
        self._sync_value_enabled()

    def _sync_value_enabled(self) -> None:
        op = self._op.currentText()
        duration = column_kind(str(self._col.currentData())) == "duration"
        self._duration_options.setVisible(duration)
        self._duration_hint.setVisible(duration)
        self._value.setPlaceholderText(
            ("Ex. : 2 ans ; 5 ans" if op == "entre" else "Ex. : 2 ans 6 mois")
            if duration else "Valeur…"
        )
        need = op not in ("est vide", "n'est pas vide")
        self._value.setEnabled(need)
        if not need:
            self._value.clear()

    def _on_add_rule(self) -> None:
        key = self._col.currentData()
        if not key:
            return
        if column_kind(str(key)) == "duration":
            parts = self._value.text().split(";")
            amounts = [_duration_months(part) for part in parts]
            expected = 2 if self._op.currentText() == "entre" else 1
            if (len(parts) != expected or any(n is None for n in amounts)
                    or (expected == 2 and amounts[0] > amounts[1])):
                QMessageBox.warning(self, "Durée invalide",
                                    "Saisissez une durée, par exemple 2 ans 6 mois. "
                                    "Pour « entre », séparez les deux durées par ; "
                                    "dans l'ordre croissant.")
                return
        rule = AdvancedFilterRule(
            column_key=str(key),
            operator=self._op.currentText(),
            value=self._value.text().strip(),
            reference_date=(self._reference.date().toString("dd/MM/yyyy")
                            if column_kind(str(key)) == "duration" and not self._today.isChecked()
                            else ""),
        )
        self._rules = [r for r in self._rules if r.column_key != rule.column_key]
        self._rules.append(rule)
        if not self._body_expanded:
            self._set_body_expanded(True)
        self._sync_summary()
        self.rules_changed.emit()

    def _on_remove_last(self) -> None:
        if not self._rules:
            return
        self._rules.pop()
        self._sync_summary()
        self.rules_changed.emit()
