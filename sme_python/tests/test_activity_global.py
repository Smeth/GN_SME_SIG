"""Global journal access and filters, using synthetic entries only."""
from datetime import date
from types import SimpleNamespace
import pytest


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFontDatabase, QFont
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    return app


def test_global_search_keeps_all_filters_and_does_not_force_sensitive_module(monkeypatch):
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    calls = []
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: True)
    monkeypatch.setattr('sme_gend.repositories.app_config_repository.AppConfigRepository',
                        lambda settings: SimpleNamespace(search_audit_journal=lambda **kw: calls.append(kw) or [],
                            list_audit_operations=lambda: [], audit_personnel_keys=lambda pid: {'career': [pid], 'legacy': [pid]}))
    query = ActivityAuditQuery(actor=' ALICE ', personnel_id='12', personnel_nom='DUPONT',
                               sme_mois='2026-09', date_from=date(2026,10,1), date_to=date(2026,10,2),
                               action='update', text='motif', limit=100)
    assert search_activity_audit(query, settings=object()) == []
    assert calls[0] == dict(module=None, action='update', actor='ALICE', personnel_id='12',
                           personnel_nom='DUPONT', sme_mois='2026-09', date_from=date(2026,10,1),
                           date_to=date(2026,10,2), text='motif', limit=100, require_table=True, broad_agent_search=True,
                           personnel_keys={'career': ['12'], 'legacy': ['12']})


def test_global_access_denied_before_query(monkeypatch):
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: False)
    with pytest.raises(PermissionError): search_activity_audit(ActivityAuditQuery(), settings=object())


def test_global_invalid_month_or_date_fails_before_query(monkeypatch):
    from sme_gend.services.activity_audit_log import ActivityAuditQuery, search_activity_audit
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: True)
    for query in (ActivityAuditQuery(sme_mois='2026-13'), ActivityAuditQuery(date_from=date(2026,10,2), date_to=date(2026,10,1))):
        with pytest.raises(ValueError): search_activity_audit(query, settings=object())


def test_global_view_supports_custom_operation_and_module_codes(app, monkeypatch, tmp_path):
    from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
    from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: False)
    view = ActivityAuditLogView()
    view._filter_action.setEditText('month_fill_completed')
    view._filter_module.setEditText('monthly_workflow')
    query = view._build_query()
    assert query.action == 'month_fill_completed' and query.module == 'monthly_workflow'
    view._filter_timer.stop()
    view._apply_entries([dict(ts_utc='2026-11-02T12:00:00Z', action='doc_gen', actor='ALICE',
        module='doc_gen', sme_mois_context='2026-09', payload=dict(doc_type='liste_present', format='pdf', count=2))], 500)
    assert 'Documents' in str(view._model._rows) if hasattr(view._model, '_rows') else view._model.rowCount() == 1
    view.resize(1250, 900)
    assert view.grab().save(str(tmp_path / 'journal_global.png'))
    print('Journal global :', tmp_path / 'journal_global.png')
    specialized = SensitiveAuditLogView()
    assert not specialized._global_activity
    assert specialized._build_query().__class__.__name__ == 'SensitiveAuditQuery'
    view.close(); specialized.close()


def test_navigation_exposes_global_journal_without_replacing_specialized_pages(app):
    from PySide6.QtWidgets import QTreeWidget
    from sme_gend.ui.sidebar_navigation import build_sidebar_nav, page_nav_icons
    from sme_gend.ui.navigation_pages import PAGE_COUNT, PAGE_ACTIVITY_AUDIT, PAGE_SENSITIVE_AUDIT, PAGE_DOC_GEN_LOG
    from sme_gend.ui.access_control import permission_for_page
    tree = QTreeWidget(); items = {}
    build_sidebar_nav(tree, style=app.style(), nav_labels=['']*PAGE_COUNT, page_icons=page_nav_icons(app.style()), page_to_item=items)
    assert all(page in items for page in (PAGE_ACTIVITY_AUDIT, PAGE_SENSITIVE_AUDIT, PAGE_DOC_GEN_LOG))
    assert permission_for_page(PAGE_ACTIVITY_AUDIT) == 'admin.sensitive_audit'
    tree.close()


def test_available_operations_include_codes_outside_the_result_limit(app):
    from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
    from sme_gend.services.activity_audit_log import ActivityEntries
    view = ActivityAuditLogView()
    view._apply_entries(ActivityEntries([], [('archive_tools', 'custom_archive_operation')]), 50)
    assert view._filter_action.findData('custom_archive_operation') >= 0
    assert view._filter_module.findData('archive_tools') >= 0
    view._filter_timer.stop(); view.close()


@pytest.mark.parametrize('width,height', [(1280, 720), (1536, 820)])
def test_global_table_gets_priority_and_advanced_filters_stay_accessible(app, monkeypatch, tmp_path, width, height):
    from sme_gend.ui.views.activity_audit_log_view import ActivityAuditLogView
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: False)
    view = ActivityAuditLogView()
    view.resize(width, height)
    view.show()
    app.processEvents()
    assert not view._advanced_scroll.isVisible()
    assert view._table.height() > height * .55
    assert view._filter_text.isVisible()
    assert view._btn_export_all.isVisible()
    assert view.grab().save(str(tmp_path / f'journal_{width}_compact.png'))
    view._advanced_toggle.click()
    app.processEvents()
    assert view._advanced_scroll.isVisible()
    assert view._table.height() > height * .25
    view._filter_actor.setText('ALICE')
    view._advanced_toggle.click()
    assert view._build_query().actor == 'ALICE'
    view._filter_timer.stop()
    view.close()
