"""One dated return dialogue and a truthful per-agent result for activity lists."""
from datetime import date

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox,
    QVBoxLayout, QWidget,
)

from sme_gend.ui.widgets.date_input import FrenchDateEdit


class ActivityReturnDialog(QDialog):
    def __init__(self, parent, *, title, agent_count, month):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(430)
        self._month = month
        self._date = FrenchDateEdit()
        from sme_gend.services.mysql_import_period import last_day_of_month
        default = min(date.today(), last_day_of_month(month)) if month else date.today()
        self._date.set_from_text(default.isoformat())
        intro = QLabel(f'{agent_count} agent(s). L’activité sera clôturée et le retour à Présent '
                       'prendra effet à la date saisie.')
        intro.setWordWrap(True)
        form = QFormLayout()
        form.addRow('Date réelle de fin / retour *', self._date)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Enregistrer le retour')
        buttons.accepted.connect(self._accept_date)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept_date(self):
        from sme_gend.services.activity_completion import validate_activity_end
        value = self._date.to_datetime()
        try:
            validate_activity_end(value.date() if value else None, self._month)
        except ValueError as exc:
            QMessageBox.warning(self, self.windowTitle(), str(exc))
            return
        self.accept()

    @staticmethod
    def run(parent, *, title, agent_count, month):
        dialog = ActivityReturnDialog(parent, title=title, agent_count=agent_count, month=month)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog._date.to_datetime().date()


def run_activity_return(parent, settings, kind, requests, *, title):
    """Return confirmed keys, or None on cancel. Failed rows stay checked."""
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    month = resolve_liste_view_mois(settings)
    actual_date = ActivityReturnDialog.run(parent, title=title, agent_count=len(requests), month=month)
    if actual_date is None:
        return None
    from sme_gend.services.operation_progress import operation_progress
    from sme_gend.ui.gui_operation_progress import gui_operation_progress
    steps = (('returns','Enregistrement et validation des retours'),('result','Préparation du bilan'))
    context = (gui_operation_progress(title,'Enregistrement des retours…',steps)
               if len(requests)>1 and isinstance(parent,QWidget) else operation_progress(steps))
    confirmed, errors, warnings = set(), [], []
    unchanged = 0
    with context as progress:
        progress.start('returns',total=len(requests))
        for index, (key, row, expected) in enumerate(requests, 1):
            label = (row.noms or row.id) if row is not None else key
            try:
                if row is None:
                    raise ValueError('Agent introuvable dans la liste affichée. Rechargez les données.')
                result = complete_activity(settings, row, kind, actual_date, expected=expected)
                confirmed.add(key)
                unchanged += not result.changed
                if result.position_admin != 'Présent':
                    warnings.append(f'{label} : retour enregistré ; position du mois : {result.position_admin or "—"}.')
            except CommittedMirrorError:
                confirmed.add(key)
                warnings.append(f'{label} : enregistrement confirmé dans MySQL ; copie locale du journal '
                                'à vérifier. Ne recommencez pas l’opération.')
            except Exception as exc:
                errors.append(f'{label} : {exc} — aucune modification enregistrée pour cet agent.')
            finally:
                progress.update('returns',current=index,total=len(requests),
                    detail=f'{index} / {len(requests)} agent(s) traité(s) ; {len(confirmed)} confirmé(s), {len(errors)} échec(s).')
        progress.finish('returns')
        progress.start('result')
        progress.finish('result',detail=f'{len(confirmed)} retour(s) confirmé(s), {len(errors)} échec(s).')
    message = f'{len(confirmed) - unchanged} retour(s) enregistré(s) au {actual_date:%d/%m/%Y}.'
    if unchanged:
        message += f'\n{unchanged} retour(s) déjà enregistré(s) à cette date, conservé(s).'
    for heading, entries in [('À vérifier', warnings), ('Échecs — lignes conservées cochées', errors)]:
        if entries:
            message += '\n\n' + heading + ' :\n' + '\n'.join(entries[:8])
            if len(entries) > 8:
                message += f'\n… et {len(entries) - 8} autre(s).'
    QMessageBox.information(parent, title, message)
    return confirmed
