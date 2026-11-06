"""Dialogues du module avancement."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QLabel,
    QMessageBox,
    QSpinBox,
    QWidget,
)

from sme_gend.models.avancement import (
    ACTE_LABELS,
    DIPLOME_LABELS,
    AvancementRegle,
)


def edit_regle(parent: QWidget, regle: AvancementRegle) -> AvancementRegle | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Règle {regle.grade_cible}")
    dialog.setMinimumWidth(460)

    def spin(value: int | None) -> QSpinBox:
        box = QSpinBox()
        box.setRange(-1, 80)
        box.setSpecialValueText("—")
        box.setValue(-1 if value is None else value)
        return box

    annees = spin(regle.annees_grade)
    ecole = spin(regle.annees_grade_ecole)
    services = spin(regle.annees_services)
    age_min = spin(regle.age_min)
    age_max = spin(regle.age_max)
    diplomes = QLineEdit(regle.diplomes_csv())
    diplomes.setPlaceholderText("Ex. OPJ  ou  OPJ, BCG  (virgules = l'un OU l'autre)")
    diplome_hint = QLabel(
        "Lien dossier : diplômes saisis dans la fiche agent (et référentiel). "
        "Codes : "
        + ", ".join(f"{k}" for k in DIPLOME_LABELS)
        + ". Séparez plusieurs codes par des virgules ou des points-virgules."
    )
    diplome_hint.setWordWrap(True)
    diplome_hint.setObjectName("gainPerteSummary")
    superieur = QCheckBox("Ou diplôme supérieur homologué")
    superieur.setChecked(regle.diplome_ou_superieur)
    tableau = QCheckBox("Inscription au tableau obligatoire")
    tableau.setChecked(regle.inscription_tableau)
    acte = QComboBox()
    acte.addItem("—", "")
    for code, label in ACTE_LABELS.items():
        acte.addItem(label, code)
    index = acte.findData(regle.acte)
    if index >= 0:
        acte.setCurrentIndex(index)
    form = QFormLayout(dialog)
    form.addRow("Années au grade", annees)
    form.addRow("Années si issu d'école", ecole)
    form.addRow("Années de services", services)
    form.addRow("Âge minimum", age_min)
    form.addRow("Âge maximum", age_max)
    form.addRow("Diplômes requis", diplomes)
    form.addRow(diplome_hint)
    form.addRow(superieur)
    form.addRow(tableau)
    form.addRow("Acte", acte)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    def optional(box: QSpinBox) -> int | None:
        return None if box.value() < 0 else box.value()

    known = set(DIPLOME_LABELS)
    codes_list: list[str] = []
    for part in diplomes.text().replace(";", ",").split(","):
        code = part.strip().upper()
        if not code:
            continue
        if code not in known:
            QMessageBox.warning(
                parent,
                "Diplômes",
                f"Code inconnu « {part.strip()} ». Codes attendus : "
                + ", ".join(sorted(known)),
            )
            return None
        if code not in codes_list:
            codes_list.append(code)
    codes = tuple(codes_list)
    return AvancementRegle(
        grade_cible=regle.grade_cible,
        grade_precedent=regle.grade_precedent,
        libelle=regle.libelle,
        annees_grade=optional(annees),
        annees_grade_ecole=optional(ecole),
        annees_services=optional(services),
        age_min=optional(age_min),
        age_max=optional(age_max),
        diplomes=codes,
        diplome_ou_superieur=superieur.isChecked(),
        inscription_tableau=tableau.isChecked(),
        voie=regle.voie,
        acte=str(acte.currentData() or ""),
        non_codifie=regle.non_codifie,
        condition_manuelle=regle.condition_manuelle,
    )
