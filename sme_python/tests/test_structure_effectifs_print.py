from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from sme_gend.config.settings import Settings
from sme_gend.services.structure_effectifs_print import structure_effectifs_html, print_structure_effectifs_document
from sme_gend.ui.views.structure_effectifs_view import StructureEffectifsView


@pytest.fixture
def app():
    from PySide6.QtGui import QFontDatabase, QFont
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/arial.ttf')
    application.setFont(QFont('Arial', 10))
    return application


def test_print_snapshot_tracks_filter_sort_and_stale_state(app):
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    view = StructureEffectifsView(settings, SimpleNamespace(settings=settings), SimpleNamespace(), lambda code: None)
    view._loaded_mois = '2026-09'
    view._view_data.finish_load()
    view._rows_data = [('B', 'Deuxième structure', 10), ('A', 'Première structure', 2), ('C', 'Autre unité', 5)]
    view._refill_table_filtered()
    view._table.sortItems(2, Qt.SortOrder.DescendingOrder)
    assert view._print_rows() == [('B', 'Deuxième structure', 10), ('C', 'Autre unité', 5), ('A', 'Première structure', 2)]
    view._search.setText('structure')
    rows = view._print_rows()
    assert len(rows) == 2 and sum(r[2] for r in rows) == 12
    assert view._btn_print.isEnabled()
    view._search.setText('introuvable')
    assert not view._btn_print.isEnabled()
    view._search.clear()
    view.mark_dirty()
    assert not view._btn_print.isEnabled()


def test_html_escapes_labels_and_prints_month_and_filtered_total():
    html = structure_effectifs_html([('A&B', '<Direction>', 12), ('C', 'Autre', 3)],
                                   mois='2026-09', search='<texte>')
    assert 'A&amp;B' in html and '&lt;Direction&gt;' in html and '&lt;texte&gt;' in html
    assert '2026-09' in html and '<b>15</b>' in html and '2 structure(s)' in html
    assert '<thead>' in html


def test_native_pdf_prints_all_rows_with_repeated_headers(app, tmp_path):
    from PySide6.QtGui import QTextDocument, QPageSize
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtPdf import QPdfDocument
    rows = [(f'S{i:03}', f'DÉSIGNATION SYNTHÉTIQUE {i}', i) for i in range(1, 121)]
    document = QTextDocument()
    document.setHtml(structure_effectifs_html(rows, mois='2026-09'))
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    path = tmp_path / 'effectifs.pdf'
    printer.setOutputFileName(str(path))
    print_structure_effectifs_document(document, printer)
    reader = QPdfDocument()
    assert reader.load(str(path)) == QPdfDocument.Error.None_
    pages = [reader.getAllText(i).text() for i in range(reader.pageCount())]
    text = '\n'.join(pages)
    assert reader.pageCount() >= 2
    assert 'S001' in text and 'S120' in text and '7260' in text and '2026-09' in text
    assert all('Désignation' in page for page in pages)
    from PySide6.QtCore import QSize
    assert reader.render(0, QSize(800, 1132)).save(str(tmp_path / 'effectifs_page1.png'))


def test_preview_uses_loaded_month_and_rows_without_reloading(app, monkeypatch):
    from PySide6.QtPrintSupport import QPrintPreviewDialog
    from sme_gend.services import structure_effectifs_print
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    view = StructureEffectifsView(settings, SimpleNamespace(settings=settings), SimpleNamespace(), lambda code: None)
    view._loaded_mois = '2025-08'
    view._view_data.finish_load()
    view._rows_data = [('A', 'TEST', 7)]
    view._refill_table_filtered()
    documents = []
    monkeypatch.setattr(structure_effectifs_print, 'print_structure_effectifs_document', lambda doc, printer: documents.append(doc.toPlainText()))
    def preview(dialog):
        dialog.paintRequested.emit(dialog.printer())
        return 0
    monkeypatch.setattr(QPrintPreviewDialog, 'exec', preview)
    view._on_print()
    assert len(documents) == 1 and '2025-08' in documents[0] and '2026-09' not in documents[0]


def test_reload_enables_print_only_after_loading_the_consulted_month(app, monkeypatch):
    from sme_gend.ui.views import structure_effectifs_view
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2025-08')
    personnel = SimpleNamespace(settings=settings, count_by_structure_code=lambda: {'A': 4, '(Sans structure)': 1})
    referentiel = SimpleNamespace(structures=lambda: [SimpleNamespace(code='A', designation='TEST', ordre_tri=1)])
    view = StructureEffectifsView(settings, personnel, referentiel, lambda code: None)
    pending = []
    monkeypatch.setattr(structure_effectifs_view, 'run_in_background', lambda parent, work, done, **kwargs: pending.append((work, done)))
    view.reload()
    assert not view._btn_print.isEnabled()
    work, done = pending.pop()
    done(work())
    assert view._loaded_mois == '2025-08' and view._btn_print.isEnabled()
    assert sum(row[2] for row in view._print_rows()) == 5
