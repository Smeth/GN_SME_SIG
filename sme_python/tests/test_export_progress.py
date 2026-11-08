"""Synthetic documents: measured rows and no success before final persistence."""
from dataclasses import fields
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.operation_progress import operation_progress, progress_reporting
from sme_gend.services.personnel_excel_export import export_liste_filtered, export_liste_by_padmin


def rows(count=250):
    empty = {field.name: None for field in fields(ListeGeneraleRow)}
    return [ListeGeneraleRow(**(empty | {"id": str(index), "noms": f"SYNTHETIC {index}",
                                       "grade": "LTN", "position_admin": "Présent" if index % 2 else "Mission"}))
            for index in range(count)]


@pytest.fixture(autouse=True)
def no_document_registry(monkeypatch):
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", lambda **kwargs: None)


def test_excel_reports_exact_rows_then_write_and_audit_before_100(monkeypatch, tmp_path):
    events = []
    path = tmp_path / "synthetic.xlsx"
    original = Workbook.save
    phases = []

    def save(book, destination):
        assert events[-1].active_key == "write" and events[-1].percent < 100
        phases.append("write")
        original(book, destination)

    def audit(**kwargs):
        assert path.is_file()
        assert events[-1].active_key == "finalize" and events[-1].percent < 100
        phases.append("audit")

    monkeypatch.setattr(Workbook, "save", save)
    monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation", audit)
    with progress_reporting(events.append):
        assert export_liste_filtered(rows(), path) == 250
    rendered = [event for event in events if event.active_key == "render" and event.current is not None]
    assert any(event.current == 100 and event.total == 250 for event in rendered)
    assert rendered[-1].current == 250 and rendered[-1].total == 250
    assert events[-1].percent == 100 and all(step.state == "done" for step in events[-1].steps)
    assert all(event.percent < 100 for event in events[:-1])
    assert phases == ["write", "audit"]
    book = load_workbook(path)
    try:
        assert book.active.max_row == 252
        assert book.active["B252"].value == "SYNTHETIC 249"
    finally:
        book.close()


def test_failed_final_write_never_reaches_100_or_reports_audit(monkeypatch, tmp_path):
    events = []
    failure = OSError("synthetic disk unavailable")
    def save(*args):
        raise failure
    monkeypatch.setattr(Workbook, "save", save)
    with progress_reporting(events.append), pytest.raises(OSError) as caught:
        export_liste_filtered(rows(3), tmp_path / "failed.xlsx")
    assert caught.value is failure
    assert events[-1].active_key == "write" and max(event.percent for event in events) < 100
    assert events[-1].steps[-1].state == "pending"


def test_grouped_excel_counts_all_rows_across_worksheets_once(tmp_path):
    events = []
    path = tmp_path / "positions.xlsx"
    with progress_reporting(events.append):
        counts = export_liste_by_padmin(rows(250), path)
    assert counts == {"Mission": 125, "Présent": 125}
    rendered = [event for event in events if event.active_key == "render" and event.current is not None]
    assert rendered[-1].current == rendered[-1].total == 250
    assert all(event.current <= event.total for event in rendered if event.total)
    book = load_workbook(path)
    try:
        assert book.sheetnames == ["Mission", "Présent", "Récapitulatif"]
    finally:
        book.close()


def test_html_generation_keeps_content_and_reports_rows():
    from sme_gend.services.liste_generale_print import build_liste_generale_print_html
    events = []
    with progress_reporting(events.append):
        output = build_liste_generale_print_html(rows(3))
    assert "SYNTHETIC 2" in output
    assert events[-1].percent == 100
    assert [step.key for step in events[0].steps] == ["prepare", "render", "finalize"]
    assert any(event.current == 3 and event.total == 3 for event in events)


def test_nested_export_does_not_finish_its_parent_before_remaining_work(tmp_path):
    events = []
    with progress_reporting(events.append), operation_progress((('read', 'Lecture'), ('render', 'Documents'), ('commit', 'Confirmation'))) as parent:
        parent.start('read'); parent.finish('read'); parent.start('render')
        export_liste_filtered(rows(2), tmp_path / 'nested.xlsx')
        assert events[-1].percent < 100 and events[-1].steps[-1].state == 'pending'
        parent.finish('render'); parent.finish('commit')
    assert events[-1].percent == 100


def test_document_job_finishes_only_after_the_document_function_returns(monkeypatch, tmp_path):
    from sme_gend.ui.document_job import run_document_job
    events = []
    path = tmp_path / 'synthetic_document.txt'
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_progress', lambda parent, title, text, work: work())
    def generate():
        assert events[-1].active_key == 'generate' and events[-1].percent < 100
        path.write_text('synthetic', encoding='utf-8')
    with progress_reporting(events.append):
        assert run_document_job(None, generate) is True
    assert path.read_text(encoding='utf-8') == 'synthetic'
    assert events[-1].percent == 100


def test_word_batch_reports_individual_files_and_merged_pages(monkeypatch, tmp_path):
    from docx import Document
    from sme_gend.services.limite_age_service import LimiteAgeService, LimiteAgeConfig
    service = LimiteAgeService()
    def individual(person, cfg, destination, *args, **kwargs):
        document = Document()
        document.add_paragraph(person.noms)
        document.save(destination)
        return destination
    monkeypatch.setattr(service, 'generate_docx', individual)
    events = []
    path = tmp_path / 'synthetic_batch.docx'
    with progress_reporting(events.append):
        assert service.generate_docx_batch(rows(3), LimiteAgeConfig(), path) == path
    assert any(event.active_key == 'render' and event.current == 3 and event.total == 3 for event in events)
    assert any(event.active_key == 'merge' and event.current == 3 and event.total == 3 for event in events)
    assert events[-1].percent == 100
    text = '\n'.join(paragraph.text for paragraph in Document(path).paragraphs)
    assert all(f'SYNTHETIC {index}' in text for index in range(3))


def test_pdf_phases_preserve_gui_thread_and_publish_only_after_render(monkeypatch, tmp_path):
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QApplication
    from sme_gend.services.document_pdf_output import save_document_pdf
    app = QApplication.instance() or QApplication([])
    events = []
    printed = []
    class Document(QTextDocument):
        def print_(self, printer):
            assert QThread.currentThread() == app.thread()
            assert events[-1].percent < 100 and 'Produire le PDF' in events[-1].detail
            printed.append(True)
            return super().print_(printer)
    document = Document()
    document.setHtml('<h1>SYNTHETIC DOCUMENT</h1>')
    path = tmp_path / 'synthetic.pdf'
    with progress_reporting(events.append), operation_progress((('pdf', 'Document PDF'), ('done', 'Confirmation'))) as parent:
        parent.start('pdf')
        assert save_document_pdf(document, path, configure=lambda *args: None, doc_type='test', count=1) == path
        assert path.read_bytes().startswith(b'%PDF') and printed == [True]
        assert events[-1].percent < 100
        parent.finish('pdf'); parent.finish('done')
    assert events[-1].percent == 100
