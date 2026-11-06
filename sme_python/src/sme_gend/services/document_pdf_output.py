"""Publish completed Qt PDF outputs before creating their central history entry."""
from pathlib import Path

def save_document_pdf(document, destination, *, configure, doc_type, count, settings=None, mois=None, persons=None, meta=None):
    from PySide6.QtPrintSupport import QPrinter
    from sme_gend.services.safe_paths import atomic_destination
    path=Path(destination)
    with atomic_destination(path, temporary_suffix=".pdf") as staged:
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(staged))
        configure(document,printer)
        document.print_(printer)
        if printer.printerState()==QPrinter.PrinterState.Error or not staged.is_file() or staged.stat().st_size==0:
            raise OSError("La génération PDF a échoué ; aucun succès enregistré dans l’historique.")
    from sme_gend.services.doc_gen_log import log_doc_generation
    log_doc_generation(doc_type=doc_type,format="pdf",count=count,dest_path=str(path),settings=settings,mois=mois,persons=persons,**({"meta":meta} if meta is not None else {}))
    return path
