"""Targeted regressions for urgency, background jobs, previews, history and rights."""
from contextlib import nullcontext
from dataclasses import fields, replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading
import time
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.models.limite_age_config import LimiteAgeConfig, GradeLimiteEntry
from sme_gend.services.limite_age_service import LimiteAgeService, LimiteDateResult
from sme_gend.ui import document_job as jobs
from sme_gend.ui.views import soit_transmis_view as st, limite_age_view as la


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def person():
    return ListeGeneraleRow(**({f.name: None for f in fields(ListeGeneraleRow)} | dict(
        id="SYN-1", noms="PERSONNE FICTIVE", grade="LTN", date_naissance=date(1990, 2, 3),
        date_service="05/06/2010", matricule_militaire="SYN-M")))


@pytest.fixture
def view(app, monkeypatch, person):
    monkeypatch.setattr(st.SoitTransmisService, "load", lambda *a: SoitTransmisConfig())
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress", lambda *a, **kw: [])
    monkeypatch.setattr(st, "can_export", lambda: True)
    monkeypatch.setattr(st, "require_export", lambda parent: True)
    widget = st.SoitTransmisView(Settings(database_path=Path("synthetic-unused")))
    widget.load_persons([person])
    yield widget
    widget.close()


@pytest.mark.parametrize("days,code,expected", [
    (-1,-1,True),(0,-1,False),(90,730,False),(364,730,False),
    (365,730,True),(729,730,True),(730,730,False),
    (0,365,True),(364,365,True),(365,365,False)])
def test_urgency_exclusive_boundaries(days,code,expected):
    today=date(2026,1,1)
    assert st._urgency_matches(today+timedelta(days=days),today,code) is expected


def test_urgency_uses_calendar_years_including_leap_day():
    assert not st._urgency_matches(date(2024,3,1),date(2023,3,2),730)
    assert st._urgency_matches(date(2024,3,2),date(2023,3,2),730)
    assert st._urgency_matches(date(2025,2,28),date(2024,2,29),730)


def test_filter_excludes_near_term_person(view,person):
    second=replace(person,id="SYN-2",noms="AUTRE PERSONNE FICTIVE")
    today=date.today()
    view._all_rows=[person,second]
    view._results={st._person_key(person):LimiteDateResult(today+timedelta(days=90),"âge",None,None,None),
                   st._person_key(second):LimiteDateResult(today+timedelta(days=500),"âge",None,None,None)}
    view._update_urgence_combo_state()
    view._combo_urgence.setCurrentIndex(view._combo_urgence.findData(730))
    assert view._model.rowCount()==1
    assert view._model._rows[0].id==second.id


def test_unique_previews_do_not_replace_an_open_document(tmp_path,monkeypatch):
    monkeypatch.setattr(jobs.tempfile,"gettempdir",lambda:str(tmp_path))
    first=jobs.preview_document("preview.docx");first.write_bytes(b"FIRST")
    second=jobs.preview_document("preview.docx");second.write_bytes(b"SECOND")
    assert first!=second and first.read_bytes()==b"FIRST"
    assert second.read_bytes()==b"SECOND"


def test_job_runs_off_gui_thread_and_event_loop_remains_responsive(app,monkeypatch):
    # Use the real worker implementation, not the immediate fixture above.
    seen=[];ticks=[];main=threading.get_ident()
    timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(True));timer.start()
    def work():
        seen.append(threading.get_ident());time.sleep(.08)
    try:
        assert jobs.run_document_job(None,work) is True
    finally:
        timer.stop()
    assert seen[0]!=main and ticks


@pytest.mark.parametrize("action",["_generate","_open_in_word","_open_both"])
def test_soit_actions_use_background_job(view,monkeypatch,tmp_path,action):
    monkeypatch.setattr(st.QFileDialog,"getSaveFileName",lambda *a:(str(tmp_path/"save.docx"),""))
    monkeypatch.setattr(st,"preview_document",lambda name:tmp_path/name)
    monkeypatch.setattr(st.QMessageBox,"information",lambda *a:None)
    opened=[];monkeypatch.setattr("os.startfile",lambda path:opened.append(path))
    produced=[];dispatched=[]
    monkeypatch.setattr(view._svc,"generate_docx",lambda people,*a,**kw:produced.append(people))
    view._la_svc=SimpleNamespace(load=lambda settings:object(),calculate_limit=lambda *a:SimpleNamespace(issues=()),
        generate_docx_batch=lambda people,*a,**kw:produced.append(people))
    def dispatch(parent,work):
        assert not produced
        dispatched.append(True);work();return True
    monkeypatch.setattr(st,"run_document_job",dispatch)
    getattr(view,action)()
    assert dispatched and len(produced)==(2 if action=="_open_both" else 1)
    assert produced[0][0] is not view._all_rows[0]  # independent snapshot
    assert len(opened)==(0 if action=="_generate" else 2 if action=="_open_both" else 1)


@pytest.mark.parametrize("action",["_generate","_open_in_word","_open_both"])
def test_denied_permission_blocks_direct_soit_actions_and_buttons(view,monkeypatch,action):
    monkeypatch.setattr(st,"can_export",lambda:False)
    monkeypatch.setattr(st,"require_export",lambda parent:False)
    view._on_check_changed()
    assert not view._btn_gen.isEnabled() and not view._btn_print_all.isEnabled()
    forbidden=MagicMock(side_effect=AssertionError("No generation without permission"))
    monkeypatch.setattr(st,"run_document_job",forbidden)
    getattr(view,action)();forbidden.assert_not_called()


@pytest.mark.parametrize("action",["_gen_docx","_gen_pdf","_print_attest"])
def test_denied_permission_blocks_individual_output(app,monkeypatch,action):
    monkeypatch.setattr(la,"require_export",lambda parent:False)
    # Denial occurs before dereferencing a person or opening a file picker.
    getattr(la._DetailPanel,action)(SimpleNamespace())


def test_checked_batch_is_dispatched_and_permission_is_rechecked(app,person,monkeypatch,tmp_path):
    service=MagicMock();tab=la._AttestationsTab(Settings(database_path=Path("unused")),service)
    tab._fresh=True
    monkeypatch.setattr(tab._model,"checked_rows",lambda:[(person,None)])
    monkeypatch.setattr(la,"require_export",lambda parent:True)
    monkeypatch.setattr(la,"preview_document",lambda name:tmp_path/name)
    monkeypatch.setattr("os.startfile",lambda *a:None)
    seen=[]
    def dispatch(parent,work):
        service.generate_docx_batch.assert_not_called();seen.append(True);work();return True
    monkeypatch.setattr(la,"run_document_job",dispatch)
    tab._print_checked();assert seen
    service.generate_docx_batch.assert_called_once()
    service.generate_docx_batch.reset_mock()
    monkeypatch.setattr(la,"require_export",lambda parent:False)
    tab._print_checked();service.generate_docx_batch.assert_not_called()
    tab.close()


def test_journal_keeps_all_people_in_central_and_mirror_payloads(monkeypatch,tmp_path):
    from sme_gend.services import doc_gen_log as log
    people=[f"PERSONNE FICTIVE {i}" for i in range(35)];seen=[];mirror=[]
    repo=SimpleNamespace(table_exists=lambda name:True,append_audit=lambda **kw:seen.append(kw))
    monkeypatch.setattr("sme_gend.repositories.app_config_repository.AppConfigRepository",lambda settings:repo)
    monkeypatch.setattr(log,"_document_write",lambda settings:nullcontext())
    monkeypatch.setattr(log,"_publish_document_revision",lambda settings:None)
    monkeypatch.setattr(log,"_json_mirror_enabled",lambda:True)
    monkeypatch.setattr(log,"_log_path",lambda:tmp_path/"synthetic-log.jsonl")
    log.log_doc_generation(doc_type="attestation_batch",format="docx",persons=people,count=35,settings=object())
    assert seen[0]["payload"]["persons"]==people
    assert seen[0]["mirror"][1][0]["persons"]==people
    assert "personne fictive 34" in log.doc_entry_search_blob(seen[0]["payload"])


def test_history_details_include_full_roster_and_mark_old_incomplete_entries():
    from sme_gend.ui.views.doc_gen_log_view import _entry_details
    people=[f"FICTIF {i}" for i in range(35)]
    text=_entry_details(dict(count=35,persons=people))
    assert "35. FICTIF 34" in text and "incomplète" not in text
    assert "incomplète" in _entry_details(dict(count=35,persons=people[:20]))


def test_permission_revoked_in_save_dialog_prevents_generation(view,monkeypatch,tmp_path):
    monkeypatch.setattr(st, "require_export", MagicMock(side_effect=[True,False]))
    monkeypatch.setattr(st.QFileDialog,"getSaveFileName",lambda *a:(str(tmp_path/"save.docx"),""))
    dispatch=MagicMock();monkeypatch.setattr(st,"run_document_job",dispatch)
    view._generate();dispatch.assert_not_called()


def test_worker_failure_reports_error_and_returns_no_success(app,monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    reported=MagicMock();monkeypatch.setattr(QMessageBox,"critical",reported)
    def fail():
        raise ValueError("SYNTHETIC FAILURE")
    assert jobs.run_document_job(None,fail) is None
    reported.assert_called_once()


def test_failed_batch_is_not_opened(view,monkeypatch,tmp_path):
    monkeypatch.setattr(st,"preview_document",lambda name:tmp_path/name)
    monkeypatch.setattr(st,"run_document_job",lambda *a:None)
    opened=MagicMock();monkeypatch.setattr("os.startfile",opened)
    view._open_in_word();opened.assert_not_called()


def test_history_pagination_preserves_filters_and_resets_on_search(app,monkeypatch):
    from sme_gend.ui.views import doc_gen_log_view as ui
    monkeypatch.setattr("sme_gend.services.auth_guard.session_can_view_doc_log",lambda:True)
    monkeypatch.setattr("sme_gend.services.auth_guard.session_can_view_attestation_history",lambda:True)
    monkeypatch.setattr("sme_gend.config.get_settings",lambda:object())
    seen=[]
    monkeypatch.setattr("sme_gend.services.doc_gen_log.doc_log_months",lambda **kw:[])
    def read(**kw):
        seen.append(kw)
        return [dict(count=1,persons=["FICTIF"],doc_type="attestation")] * (2000 if not kw.get("offset") else 1)
    monkeypatch.setattr("sme_gend.services.doc_gen_log.read_doc_log",read)
    monkeypatch.setattr(ui,"run_in_background",lambda owner,work,done,**kw:done(work()))
    screen=ui.DocGenLogView()
    screen._filter_search.setText("FICTIF")
    screen.refresh()
    assert screen._next_page.isEnabled() and not screen._previous_page.isEnabled()
    screen._change_page(1)
    assert seen[-1]["offset"]==2000 and seen[-1]["search_query"]=="FICTIF"
    assert screen._previous_page.isEnabled() and not screen._next_page.isEnabled()
    screen._filter_search.setText("AUTRE")
    assert screen._page_offset==0
    screen._filter_timer.stop();screen._revision_timer.stop();screen.close()


def test_repository_pagination_is_parameterized_after_scope_filters(monkeypatch):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    repo=AppConfigRepository.__new__(AppConfigRepository)
    cursor=MagicMock();cursor.__enter__.return_value=cursor;cursor.fetchall.return_value=[]
    conn=MagicMock();conn.__enter__.return_value=conn;conn.cursor.return_value=cursor
    repo._mc=SimpleNamespace(connect=lambda **kwargs:conn)
    monkeypatch.setattr(repo,"table_exists",lambda name:True)
    assert repo.read_doc_gen_tail(2000,doc_types=["attestation"],search_query="FICTIF",offset=2000)==[]
    sql,params=cursor.execute.call_args.args
    assert "OFFSET %s" in sql and params[-2:]==(2000,2000)
    assert "attestation" in params and "%FICTIF%" in params
    with pytest.raises(ValueError):repo.read_doc_gen_tail(offset=-1)


@pytest.mark.parametrize("kind",["batch","soit"])
def test_real_generators_record_more_than_twenty_people(tmp_path,person,monkeypatch,kind):
    from sme_gend.services.soit_transmis_service import SoitTransmisService
    root=Path(__file__).resolve().parents[1]
    monkeypatch.setattr("sme_gend.config.settings.resolve_modele_file",lambda name:root/"modeles"/name)
    monkeypatch.setattr("sme_gend.services.limite_age_service._resolve_service_line",lambda *a:("","",""))
    seen=[];monkeypatch.setattr("sme_gend.services.doc_gen_log.log_doc_generation",lambda **kw:seen.append(kw))
    people=[replace(person,id=str(i),noms=f"FICTIF {i}") for i in range(21)]
    if kind=="batch":
        LimiteAgeService().generate_docx_batch(people,LimiteAgeConfig(grades=[GradeLimiteEntry("LTN",55,37)]),tmp_path/"batch.docx")
    else:
        SoitTransmisService().generate_docx(people,SoitTransmisConfig(),tmp_path/"soit.docx")
    assert len(seen)==1 and len(seen[0]["persons"])==21
    assert set(seen[0]["persons"])=={p.noms for p in people}
