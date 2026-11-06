"""Aperçu et validation explicite d'un rapprochement de fiches."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QFormLayout, QComboBox, QPlainTextEdit,
    QLineEdit, QCheckBox, QPushButton, QDialogButtonBox, QMessageBox,
)
from sme_gend.services.personnel_merge_service import preview_personnel_merge, apply_personnel_merge
from sme_gend.ui.bg_task import run_in_background
from sme_gend.ui.confirm_dialogs import confirm_yes_no
from sme_gend.services.personnel_identity_assessment import assess_identity


def describe_merge(preview):
    names = {"carriere_situation": "Situations mensuelles", "agent_diplome": "Diplômes",
             "agent_passation_grade": "Grades et nominations", "agent_affectation": "Affectations",
             "parcours_stage": "Stages", "mouvement_effectif": "Mouvements",
             "agent_mission": "Missions", "agent_malade": "Périodes de maladie",
             "agent_desertion": "Désertions", "agent_conge": "Congés",
             "agent_distinction": "Médailles et décorations", "agent_position_event": "Positions administratives",
             "avancement_ligne": "Lignes d’avancement"}
    lines = [f"Fiche à conserver : {preview.target} — {preview.target_name}",
             f"Fiche à rattacher : {preview.source} — {preview.source_name}",
             "", "SITUATIONS ET HISTORIQUES À TRANSFÉRER"]
    lines.extend(f"• {names.get(table, table)} : {count} ligne(s), identifiants et contenu conservés" for table, count in preview.counts.items() if count)
    lines.append("Mois de carrière transférés : " + (", ".join(preview.months) or "aucun"))
    if preview.fills:
        lines.extend(["", "CHAMPS VIDES DE LA CIBLE COMPLÉTÉS", *(f"{k} : {v}" for k, v in preview.fills.items())])
    lines.extend(["", "CONFLITS BLOQUANTS", *(preview.conflicts or ("Aucun conflit détecté dans cet aperçu.",)),
                  "", "CONSERVATION ET TRAÇABILITÉ", *preview.notes,
                  "La fiche source sera retirée des identités actives après transfert ; son identité originale sera archivée. "
                  "Ses anciens identifiants resteront reconnus lors des réimports. Les médias ne sont pas supprimés.",
                  "Une nouvelle sauvegarde complète sera créée avant toute modification. "
                  "Les données et les conflits seront revérifiés dans la transaction avant transfert."])
    return "\n".join(lines)


class PersonnelMergeDialog(QDialog):
    def __init__(self, parent, settings, identities, target):
        super().__init__(parent)
        self.setWindowTitle("Rapprocher deux fiches — aperçu obligatoire")
        self.resize(950, 730)
        self._settings = settings
        self._preview = None
        self._busy = False
        self._generation = 0
        self._closed = False
        self._identities = {identity.legacy_id: identity for identity in identities}
        layout = QVBoxLayout(self)
        intro = QLabel("Choisissez la fiche à conserver et celle dont les situations seront rattachées. "
                       "Un conflit bloque l’opération : aucun historique existant ne sera écrasé.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.target = QComboBox()
        self.source = QComboBox()
        for identity in identities:
            for combo in (self.target, self.source):
                combo.addItem(f"{identity.legacy_id} — {identity.noms or 'Nom non renseigné'}", identity.legacy_id)
        self.target.setCurrentIndex(max(0, self.target.findData(target)))
        other = next((i for i in range(self.source.count()) if self.source.itemData(i) != target), 0)
        self.source.setCurrentIndex(other)
        form = QFormLayout()
        form.addRow("Conserver la fiche", self.target)
        form.addRow("Rattacher cette fiche", self.source)
        layout.addLayout(form)
        self.preview_button = QPushButton("Calculer l’aperçu et contrôler les conflits")
        self.preview_button.clicked.connect(self._load_preview)
        layout.addWidget(self.preview_button)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setPlainText("Aucun transfert n’a été effectué. Calculez d’abord l’aperçu.")
        layout.addWidget(self.summary, 1)
        self.reason = QLineEdit()
        self.reason.setMaxLength(2000)
        self.reason.setPlaceholderText("Motif et références des vérifications effectuées (obligatoire)")
        layout.addWidget(self.reason)
        self.confirmed = QCheckBox("J’ai vérifié les pièces et les deux historiques : ces fiches désignent la même personne.")
        layout.addWidget(self.confirmed)
        self.apply_button = QPushButton("Sauvegarder puis rattacher les situations et historiques")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._apply)
        layout.addWidget(self.apply_button)
        self.reason.textChanged.connect(self._buttons)
        self.confirmed.toggled.connect(self._buttons)
        for combo in (self.source, self.target):
            combo.currentIndexChanged.connect(self._changed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        assessment = self._identity_assessment()
        if assessment is not None and not assessment.eligible:
            self.summary.setPlainText(assessment.label + ". " + assessment.explanation)
        self._buttons()

    def _identity_assessment(self):
        source = self._identities.get(self.source.currentData())
        target = self._identities.get(self.target.currentData())
        if source is None or target is None or self.source.currentData() == self.target.currentData():
            return None
        return assess_identity(source, target)

    def _buttons(self, *_):
        assessment = self._identity_assessment()
        eligible = assessment is not None and assessment.eligible
        self.apply_button.setEnabled(not self._busy and self._preview is not None and not self._preview.conflicts
                                     and eligible and self.confirmed.isChecked() and bool(self.reason.text().strip()))
        self.preview_button.setEnabled(not self._busy and eligible)
        self.source.setEnabled(not self._busy)
        self.target.setEnabled(not self._busy)
        self.reason.setEnabled(not self._busy)
        self.confirmed.setEnabled(not self._busy)

    def _changed(self, *_):
        self._generation += 1
        self._preview = None
        self.confirmed.setChecked(False)
        assessment = self._identity_assessment()
        self.summary.setPlainText("Le choix des fiches a changé. Recalculez l’aperçu." if assessment and assessment.eligible else
                                 (assessment.label + ". " + assessment.explanation if assessment else "Choisissez deux fiches distinctes."))
        self._buttons()

    def _load_preview(self):
        if self._busy:
            return
        assessment = self._identity_assessment()
        if assessment is None or not assessment.eligible:
            self._changed()
            return
        self._busy = True
        self._preview = None
        self.confirmed.setChecked(False)
        self._buttons()
        generation = self._generation
        source, target = self.source.currentData(), self.target.currentData()
        self.summary.setPlainText("Contrôle des identités, périodes, événements et contraintes…")
        def loaded(preview):
            self._busy = False
            if self._closed or generation != self._generation:
                if not self._closed:
                    self._buttons()
                return
            self._preview = preview
            self.summary.setPlainText(describe_merge(preview))
            self._buttons()
        run_in_background(self, lambda: preview_personnel_merge(self._settings, source, target), loaded, on_error=self._failed)

    def _failed(self, error):
        self._busy = False
        self._preview = None
        if not self._closed:
            self.summary.setPlainText(str(error) + "\nL’opération n’a pas été confirmée ; actualisez l’aperçu avant de réessayer.")
            self._buttons()

    def _apply(self):
        if not self.apply_button.isEnabled():
            return
        preview, reason = self._preview, self.reason.text().strip()
        if not confirm_yes_no(self, "Valider le rapprochement",
            f"Rattacher la fiche {preview.source} à la fiche {preview.target} ?\n"
            "La source sera archivée puis retirée des identités actives. Ses historiques seront transférés. "
            "Une sauvegarde préalable et une trace d’audit seront conservées."):
            return
        self._busy = True
        self._buttons()
        self.summary.appendPlainText("\nSauvegarde et rapprochement en cours. Ne relancez pas cette opération.")
        def finished(result):
            self._busy = False
            if not self._closed:
                QMessageBox.information(self, "Rapprochement terminé",
                    f"Les historiques de {result['source']} sont rattachés à {result['target']}.\nSauvegarde : {result['backup']}")
                self.accept()
        run_in_background(self, lambda: apply_personnel_merge(self._settings, preview, reason=reason), finished, on_error=self._failed)

    def done(self, result):
        if self._busy:
            return
        self._closed = True
        super().done(result)
