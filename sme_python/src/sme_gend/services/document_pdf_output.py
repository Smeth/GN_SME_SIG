"""Publish completed Qt PDF outputs before creating their central history entry."""
from pathlib import Path
from sme_gend.services.operation_progress import operation_progress, current_operation_progress

def save_document_pdf(document, destination, *, configure, doc_type, count, settings=None, mois=None, persons=None, meta=None):
    # QPrinter and the supplied QTextDocument stay on their caller's GUI thread.
    # Qt has no per-page print callback: report completed phases, never guessed
    # page percentages or event pumping during document.print_().
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    steps=(('prepare','Configurer la mise en page PDF'),
           ('render','Produire le PDF'),
           ('verify','Vérifier et publier le fichier PDF'),
           ('finalize','Journaliser le document PDF'))
    app=QApplication.instance()
    if app is not None and QThread.currentThread()==app.thread() and current_operation_progress() is None:
        from sme_gend.ui.gui_operation_progress import gui_operation_progress
        context=gui_operation_progress('Export PDF','Production du document PDF…',steps)
    else:
        context=operation_progress(steps)
    with context as progress:
        return _save_document_pdf(document,destination,configure=configure,doc_type=doc_type,count=count,
            settings=settings,mois=mois,persons=persons,meta=meta,progress=progress)


def _save_document_pdf(document, destination, *, configure, doc_type, count, settings, mois, persons, meta, progress):
    from PySide6.QtPrintSupport import QPrinter
    from sme_gend.services.safe_paths import atomic_destination
    path=Path(destination)
    progress.start('prepare')
    with atomic_destination(path, temporary_suffix=".pdf") as staged:
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(staged))
        configure(document,printer)
        progress.start('render',total=1,detail='Rendu Qt et écriture du document ; attendez la fin de cette étape.')
        document.print_(printer)
        progress.start('verify',total=1,detail='Contrôle du fichier produit avant publication')
        if printer.printerState()==QPrinter.PrinterState.Error or not staged.is_file() or staged.stat().st_size==0:
            raise OSError("La génération PDF a échoué ; aucun succès enregistré dans l’historique.")
    progress.start('finalize',detail='PDF final enregistré ; historique du document')
    from sme_gend.services.doc_gen_log import log_doc_generation
    log_doc_generation(doc_type=doc_type,format="pdf",count=count,dest_path=str(path),settings=settings,mois=mois,persons=persons,**({"meta":meta} if meta is not None else {}))
    progress.finish('finalize')
    return path
