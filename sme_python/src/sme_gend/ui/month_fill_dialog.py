"""Assistant commun au cycle mensuel et au menu Fichier."""
from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)
from sme_gend.services import month_fill as service
from sme_gend.services.sme_month_closure import shift_sme_month_yyyy_mm
from sme_gend.ui.bg_task import run_with_progress
from sme_gend.ui.confirm_dialogs import confirm_yes_no


class MonthFillDialog(QDialog):
    def __init__(self, parent, settings, sources):
        super().__init__(parent)
        self._settings, self._plan, self.result_data = settings, None, None
        self.setWindowTitle('Remplir un mois à partir d’un autre')
        self.resize(720, 690)
        root = QVBoxLayout(self)
        hint = QLabel('Reprenez les effectifs et leur situation dans un mois vide, sans fichier à importer. '
                      'Les identités et les historiques des agents sont conservés. '
                      'Le mois source reste disponible et le mois actif ne change pas.')
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        self._source = QComboBox()
        for month, count in sources:
            self._source.addItem(f'{month} — {count} agent(s)', month)
        self._target = QDateEdit()
        self._target.setDisplayFormat('MMMM yyyy')
        self._target.setCalendarPopup(True)
        self._target.setDateRange(QDate(2000, 1, 1), QDate(2100, 12, 31))
        form.addRow('Mois source :', self._source)
        form.addRow('Mois à remplir :', self._target)
        root.addLayout(form)
        group = QGroupBox('Activités à reprendre si leurs dates couvrent le mois cible')
        choices = QVBoxLayout(group)
        self._options = {}
        for key, label in (('stages', 'Stages'), ('missions', 'Missions'),
                           ('conges', 'Congés'), ('maladies', 'Maladies')):
            box = QCheckBox(label)
            box.setChecked(False)
            box.toggled.connect(self._invalidate)
            choices.addWidget(box)
            self._options[key] = box
        dates_hint = QLabel('Une fin inconnue est signalée dans l’aperçu. Les activités dont les dates '
                            'se limitent au mois source ne sont pas prolongées automatiquement. '
                            'Une position sans activité reprise sera marquée « À vérifier ».')
        dates_hint.setWordWrap(True)
        choices.addWidget(dates_hint)
        root.addWidget(group)
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setPlaceholderText('Choisissez les deux mois et les options, puis cliquez sur « Voir l’aperçu ».')
        root.addWidget(self._preview, 1)
        buttons = QHBoxLayout()
        self._preview_button = QPushButton('Voir l’aperçu')
        self._preview_button.clicked.connect(self._load_preview)
        self._apply_button = QPushButton('Remplir le mois…')
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply)
        cancel = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel.button(QDialogButtonBox.StandardButton.Cancel).setText('Annuler')
        cancel.rejected.connect(self.reject)
        buttons.addWidget(self._preview_button)
        buttons.addStretch()
        buttons.addWidget(self._apply_button)
        buttons.addWidget(cancel)
        root.addLayout(buttons)
        self._target.dateChanged.connect(self._invalidate)
        self._source.currentIndexChanged.connect(self._source_changed)
        preferred = settings.mysql_view_mois
        index = self._source.findData(preferred)
        if index >= 0:
            self._source.setCurrentIndex(index)
        self._source_changed()

    def _invalidate(self, *_):
        self._plan = None
        self._apply_button.setEnabled(False)
        self._preview.clear()

    def _source_changed(self, *_):
        source = self._source.currentData()
        if source:
            target = shift_sme_month_yyyy_mm(source, delta_months=1)
            self._target.setDate(QDate(int(target[:4]), int(target[5:7]), 1))
        self._invalidate()

    def _load_preview(self):
        self._invalidate()
        source = self._source.currentData()
        target = self._target.date().toString('yyyy-MM')
        options = service.FillOptions(**{key: box.isChecked() for key, box in self._options.items()})
        try:
            plan = run_with_progress(self, 'Aperçu de la reprise', 'Vérification des données des deux mois…',
                                     lambda: service.preview_month_fill(self._settings, source, target, options))
        except Exception as exc:
            QMessageBox.warning(self, 'Reprise impossible', str(exc))
            return
        if plan is None:
            return
        self._plan = plan
        lines = [f'Mois source : {plan.source}', f'Mois à remplir : {plan.target}', '']
        lines.extend(f'{service.LABELS[table]} : {count}' for table, count in plan.counts)
        lines.extend((f'Agents exclus (départs ou pertes) : {plan.excluded}', '', *plan.warnings))
        if plan.review:
            lines.extend(('', 'Positions à vérifier (20 premières) :'))
            lines.extend(f'- {name} : {position}' for _, name, position in plan.review[:20])
        if plan.conflicts:
            lines.extend(('', 'REPRISE BLOQUÉE :', *plan.conflicts))
        else:
            lines.extend(('', 'Une sauvegarde sera créée avant l’enregistrement. '
                           'Aucun diplôme, grade ou événement d’affectation supplémentaire ne sera créé.'))
        self._preview.setPlainText('\n'.join(lines))
        self._apply_button.setEnabled(not plan.conflicts)

    def _apply(self):
        plan = self._plan
        if plan is None or plan.conflicts:
            return
        count = dict(plan.counts)['carriere_situation']
        if not confirm_yes_no(self, 'Confirmer la reprise',
                              f'Reprendre {count} agent(s) de {plan.source} vers {plan.target}, avec les options de l’aperçu ?\n\n'
                              'Une sauvegarde sera créée. Le mois source et le mois actif sont conservés.', default_no=True):
            return
        self._apply_button.setEnabled(False)
        try:
            from sme_gend.ui.bg_task import run_with_determinate_progress
            result = run_with_determinate_progress(self, 'Remplissage de la SME',
                service.month_fill_progress_steps(plan),
                lambda report: service.apply_month_fill(self._settings, plan, progress=report))
        except Exception as exc:
            self._invalidate()
            QMessageBox.critical(self, 'Reprise non confirmée', str(exc))
            return
        if result is None:
            self._invalidate()
            return
        self.result_data = result
        QMessageBox.information(self, 'Mois rempli',
                                f'Le mois {result["target"]} a été rempli depuis {result["source"]}.\n'
                                f'Positions à vérifier : {result["review"]}.\n'
                                'Vous pouvez consulter ce mois puis l’activer lorsque vous souhaitez y travailler.\n'
                                f'Sauvegarde : {result["backup"]}')
        self.accept()


def run_month_fill_dialog(parent, settings):
    if not service.can_fill_month():
        QMessageBox.warning(parent, 'Reprise mensuelle', 'Cette action exige un administrateur autorisé à importer les données mensuelles.')
        return False
    try:
        sources = run_with_progress(parent, 'Reprise mensuelle', 'Chargement des mois disponibles…',
                                    lambda: service.list_month_sources(settings))
    except Exception as exc:
        QMessageBox.critical(parent, 'Reprise mensuelle', str(exc))
        return False
    if sources is None:
        return False
    if not sources:
        QMessageBox.information(parent, 'Reprise mensuelle', 'Aucun mois ne contient d’effectifs à reprendre.')
        return False
    dialog = MonthFillDialog(parent, settings, sources)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    window = parent.window()
    if hasattr(window, 'refresh_for_month_change'):
        window.refresh_for_month_change()
    return True
