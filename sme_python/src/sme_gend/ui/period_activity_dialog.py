"""A compact date interval for a report distinct from the official monthly SME."""
from datetime import date
from pathlib import Path
from PySide6.QtWidgets import QDialog,QDialogButtonBox,QFormLayout,QLabel,QVBoxLayout,QFileDialog,QMessageBox
from sme_gend.ui.widgets.date_input import FrenchDateEdit


def open_period_activity_export(parent,settings):
    from sme_gend.services.period_activity_report import _permissions,export_period_activity
    try:_permissions()
    except PermissionError as exc:
        QMessageBox.warning(parent,'Rapport de période',str(exc));return
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.mysql_import_period import first_day_of_month,last_day_of_month
    month=resolve_export_mois(settings) or date.today().strftime('%Y-%m')
    dialog=QDialog(parent);dialog.setWindowTitle('Mouvements sur une période');dialog.setMinimumWidth(460)
    start,end=FrenchDateEdit(),FrenchDateEdit()
    start.set_from_text(first_day_of_month(month).isoformat());end.set_from_text(last_day_of_month(month).isoformat())
    form=QFormLayout();form.addRow('Du *',start);form.addRow('Au *',end)
    info=QLabel('Événements datés et effectif mensuel de référence. Les effectifs de plusieurs mois ne sont pas additionnés.')
    info.setWordWrap(True)
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Exporter le rapport Excel…')
    def accept():
        if start.to_datetime() is None or end.to_datetime() is None or start.to_datetime()>end.to_datetime():
            QMessageBox.warning(dialog,'Période','Renseignez deux dates valides, dans l’ordre.');return
        dialog.accept()
    buttons.accepted.connect(accept);buttons.rejected.connect(dialog.reject)
    layout=QVBoxLayout(dialog);layout.addWidget(info);layout.addLayout(form);layout.addWidget(buttons)
    if dialog.exec()!=QDialog.DialogCode.Accepted:return
    destination,_=QFileDialog.getSaveFileName(parent,'Rapport de période',
        f'Mouvements_{start.to_datetime():%Y%m%d}_{end.to_datetime():%Y%m%d}.xlsx','Excel (*.xlsx)')
    if not destination:return
    from sme_gend.ui.confirm_dialogs import confirm_overwrite_file
    if not confirm_overwrite_file(parent,Path(destination)):return
    from sme_gend.ui.bg_task import run_with_progress
    result=run_with_progress(parent,'Rapport de période','Lecture cohérente et création du rapport…',
        lambda:export_period_activity(settings,start.to_datetime(),end.to_datetime(),destination))
    if result is not None:
        message=f'{len(result["events"])} événement(s) daté(s).\n{destination}'
        if result.get('journal_warning'):
            QMessageBox.warning(parent,'Rapport enregistré',message+'\n\n'+result['journal_warning'])
        else:
            QMessageBox.information(parent,'Rapport enregistré',message)
