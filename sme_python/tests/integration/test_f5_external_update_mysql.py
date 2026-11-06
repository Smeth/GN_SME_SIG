from types import SimpleNamespace
from unittest.mock import Mock
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def test_f5_reloads_real_mysql_rows_without_revision_change(isolated_mysql, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.shell_window import ShellWindow
    from sme_gend.ui.views.personnel_list_view import PersonnelListView
    from sme_gend.services.data_sync import read_data_revision
    from sme_gend.services.liste_generale_cache import get_liste_rows
    app = QApplication.instance() or QApplication([])
    settings, connect, params, personnel, _ = setup_services(isolated_mysql, monkeypatch)
    previous = get_liste_rows(settings, personnel.liste_complete)[0].noms
    revision = read_data_revision(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE agent SET noms='UPDATED FROM OTHER PC' WHERE id=1")
    assert read_data_revision(settings) == revision
    assert get_liste_rows(settings, personnel.liste_complete)[0].noms == previous
    applied = []
    page = SimpleNamespace(_settings=settings, _service=personnel, _apply_loaded_rows=applied.append,
                           _refresh_lock_banner=Mock(), mark_view_stale=Mock())
    page.refresh_from_database = lambda: PersonnelListView._refresh_all(page)
    shell = SimpleNamespace(_shared_revision_busy=False, _shared_revision=revision, _settings=settings,
        _stack=Mock(), _page_widget=lambda *a, **kw: page, _has_unsaved_changes=lambda: False,
        refresh_for_month_change=Mock(), _context_banner=Mock(), statusBar=Mock())
    shell._reload_current_view = lambda: ShellWindow._reload_current_view(shell)
    monkeypatch.setattr('sme_gend.ui.bg_task.run_in_background', lambda parent, work, done, **kw: done(work()))
    monkeypatch.setattr('sme_gend.ui.views.personnel_list_view.run_with_progress', lambda parent, title, text, work, **kw: work())
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.peek_editing_state', lambda *a, **kw: None)
    ShellWindow._poll_shared_data_revision(shell, refresh=True)
    assert len(applied) == 1
    assert applied[0][0].noms == 'UPDATED FROM OTHER PC'
    shell.refresh_for_month_change.assert_not_called()
