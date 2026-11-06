from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest


@pytest.fixture
def alert_cache(monkeypatch):
    from sme_gend.services import career_alerts_cache as cache
    monkeypatch.setattr(cache,'_cache',{})
    monkeypatch.setattr(cache,'_inflight',{})
    monkeypatch.setattr(cache,'_generation',0)
    monkeypatch.setattr(cache,'_cache_key',lambda settings:(settings,'2026-09'))
    return cache


@pytest.mark.parametrize('error',[KeyboardInterrupt,RuntimeError])
def test_interrupted_alert_owner_always_releases_waiters(alert_cache,monkeypatch,error):
    cache=alert_cache
    def failed(*a,**kw):raise error('SYNTHETIC')
    monkeypatch.setattr(cache,'scan_all_career_alerts',failed)
    with pytest.raises(error):cache.scan_and_cache_career_alerts('SYNTHETIC')
    assert not cache._inflight
    assert not cache.get_career_alerts_snapshot('SYNTHETIC').scanning
    monkeypatch.setattr(cache,'scan_all_career_alerts',lambda *a,**kw:[])
    assert cache.scan_and_cache_career_alerts('SYNTHETIC').ready


def test_alert_wait_is_bounded_without_stealing_the_owner(alert_cache,monkeypatch):
    cache=alert_cache;event=Event()
    cache._inflight[('SYNTHETIC','2026-09')]=(0,event)
    monkeypatch.setattr(cache,'_WAIT_SECONDS',0.001)
    with pytest.raises(TimeoutError):cache.scan_and_cache_career_alerts('SYNTHETIC')
    assert cache._inflight[('SYNTHETIC','2026-09')]==(0,event)


def test_invalidation_wakes_waiters_and_prevents_stale_publication(alert_cache,monkeypatch):
    cache=alert_cache;started=Event();release=Event();calls=[]
    def scan(*a,**kw):
        calls.append(True)
        if len(calls)==1:
            started.set();assert release.wait(3)
            return [SimpleNamespace(severity='warning')]
        return []
    monkeypatch.setattr(cache,'scan_all_career_alerts',scan)
    with ThreadPoolExecutor(max_workers=1) as pool:
        owner=pool.submit(cache.scan_and_cache_career_alerts,'SYNTHETIC')
        assert started.wait(3)
        completed=cache._inflight[('SYNTHETIC','2026-09')][1]
        cache.invalidate_career_alerts_cache()
        assert completed.is_set()
        release.set()
        assert owner.result(timeout=3).total==0
    assert cache.get_career_alerts_snapshot('SYNTHETIC').total==0


def test_reference_failure_is_not_replaced_with_observed_positions():
    from sme_gend.services.referentiel_service import ReferentielService
    svc=ReferentielService.__new__(ReferentielService)
    svc._padmin=SimpleNamespace(fetch_all=MagicMock(side_effect=RuntimeError('SYNTHETIC')))
    svc._liste_generale=MagicMock()
    with pytest.raises(RuntimeError):svc.padmins()
    with pytest.raises(RuntimeError):svc.position_admin_choices()
    svc._liste_generale.fetch_distinct_padmin.assert_not_called()


def test_failed_identity_refresh_does_not_open_a_stale_identity():
    from sme_gend.ui.personnel_dossier_open import resolve_dossier_row
    svc=SimpleNamespace(get_by_id=MagicMock(side_effect=RuntimeError('SYNTHETIC')))
    with pytest.raises(RuntimeError,match='Identité non actualisée'):
        resolve_dossier_row(svc,SimpleNamespace(id='7'))


def test_failed_conge_read_is_displayed_as_unavailable(monkeypatch):
    from sme_gend.services.dashboard_service import build_conge_long_alert_kpi_card
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.get_liste_rows',MagicMock(side_effect=RuntimeError('SYNTHETIC')))
    from sme_gend.config.settings import Settings
    card=build_conge_long_alert_kpi_card(Settings(database_path=Path('unused')))
    assert card is not None and 'Indisponible' in str(card)


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_failed_dossier_table_cannot_look_like_an_empty_history(app):
    from PySide6.QtWidgets import QLabel,QPushButton,QTableWidget
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    dlg=PersonnelDossierDialog.__new__(PersonnelDossierDialog)
    dlg._read_errors={};dlg._read_error_label=QLabel();dlg._read_retry=QPushButton()
    dlg._row=object();dlg._tbl_diplomes=QTableWidget(0,8)
    dlg._dossier_svc=SimpleNamespace(list_diplomes=MagicMock(side_effect=RuntimeError('SYNTHETIC')))
    dlg._refresh_diplomes_tab()
    assert dlg._tbl_diplomes.rowCount()==1
    assert 'indisponibles' in dlg._tbl_diplomes.item(0,0).text()
    assert 'Diplômes' in dlg._read_errors
    assert not dlg._diplome_rows


def test_sync_error_and_deferred_states_keep_last_success(app):
    from sme_gend.ui.shell_window import ShellWindow
    from PySide6.QtWidgets import QLabel
    probe=SimpleNamespace(_sync_status_label=QLabel())
    ShellWindow._set_shared_sync_status(probe,'ok')
    last=probe._sync_last_success
    ShellWindow._set_shared_sync_status(probe,'error')
    assert probe._sync_last_success==last and 'erreur' in probe._sync_status_label.text()
    ShellWindow._set_shared_sync_status(probe,'deferred')
    assert probe._sync_last_success==last and 'différée' in probe._sync_status_label.text()


def test_global_page_navigation_resets_when_filters_change(app):
    from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
    from sme_gend.services.activity_audit_log import ActivityPage
    view=ActivityAuditLogView()
    entries=ActivityPage([dict(ts_utc='2026-11-02T00:00:00+00:00',journal_id=10)],[],snapshot_id=20,has_more=True)
    view._apply_entries(entries,1)
    assert view._next_cursor is not None and view._btn_next.isEnabled()
    view._page_cursors.append(view._next_cursor)
    view._filters_changed()
    assert view._page_cursors==[None] and view._page_snapshot is None
    view._filter_timer.stop();view.close()
