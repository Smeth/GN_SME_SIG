from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget, QVBoxLayout


@pytest.fixture
def app():
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    application.setFont(QFont('Segoe UI', 10))
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(application, 'light')
    return application


@pytest.mark.parametrize('width', [900, 1600])
def test_dashboard_chart_cards_share_width_and_remain_reachable(app, tmp_path, width):
    from test_dashboard_stats_panel import _snapshot
    from sme_gend.config.settings import Settings
    from sme_gend.ui.views.dashboard_stats_panel import DashboardStatsPanel
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    panel = DashboardStatsPanel(Settings(database_path=Path('unused')))
    panel.update(_snapshot())
    scroll.setWidget(panel)
    scroll.resize(width, 800)
    scroll.show()
    for _ in range(4):
        app.processEvents()
    assert scroll.horizontalScrollBar().maximum() == 0
    charts = [panel._charts_layout.itemAt(i).widget() for i in range(4)]
    if width >= 1000:
        assert abs(charts[0].width() - charts[1].width()) < 4
        assert charts[1].width() > width * .4
    else:
        assert panel._charts_layout.getItemPosition(1)[0] == 1
        assert all(chart.width() > width * .8 for chart in charts)
    scroll.ensureWidgetVisible(panel._charts_wrap)
    app.processEvents()
    assert scroll.grab().save(str(tmp_path / f'dashboard_{width}.png'))
    scroll.close()


@pytest.mark.parametrize('width,height', [(1100, 720), (1536, 820)])
def test_sensitive_audit_prioritizes_table_and_retains_hidden_filters(app, monkeypatch, tmp_path, width, height):
    from sme_gend.ui.views.sensitive_audit_log_view import SensitiveAuditLogView
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_view_sensitive_audit', lambda: False)
    page = SensitiveAuditLogView()
    page.resize(width, height)
    page.show()
    app.processEvents()
    assert page._table.height() > height * .55
    assert not page._advanced_scroll.isVisible()
    page._advanced_toggle.click()
    page._filter_actor.setText('admin')
    page._advanced_toggle.click()
    assert page._build_query().actor == 'admin'
    page._filter_timer.stop()
    app.processEvents()
    assert page._table.height() > height * .55
    assert page.grab().save(str(tmp_path / f'audit_sensitive_{width}.png'))
    page.close()


def test_structures_compact_header_and_secondary_actions(app, monkeypatch, tmp_path):
    from sme_gend.config.settings import Settings
    from sme_gend.models.structure import StructureRow
    from sme_gend.ui.views import structure_hub_view, organigram_quality_banner
    from sme_gend.ui.views.referentiel_views import StructuresView
    monkeypatch.setattr(StructuresView, 'on_page_activated', lambda self: None)
    settings = Settings(database_path=Path('unused'))
    service = SimpleNamespace(settings=settings)
    for module in (structure_hub_view, organigram_quality_banner):
        monkeypatch.setattr(module, 'StructureOrganigramService', lambda settings: SimpleNamespace(settings=settings))
    monkeypatch.setattr(structure_hub_view, 'evaluate_editing_state', lambda settings: SimpleNamespace(locked=False))
    page = StructuresView(service)
    hub = page._hub_panel
    hub._structures = [StructureRow('DPF', 'DIRECTION DU PERSONNEL ET DE LA FORMATION', 1, 'DIRECTION'),
                       StructureRow('DSF', 'DIRECTION DES SERVICES FINANCIERS', 2, 'DIRECTION')]
    hub._view_data.finish_load()
    hub._refill_table()
    page.resize(1500, 820)
    page.show()
    app.processEvents()
    assert not page._quality_banner.isVisible()
    assert not hub._btn_refresh.isVisible()
    assert hub._table.height() > page.height() * .65
    assert hub._table.width() > hub._split.width() * .6
    assert len(hub._more_actions.menu().actions()) == 4
    hub._more_actions.menu().actions()[0].trigger()
    assert hub._checked_codes == {'DPF', 'DSF'}
    assert page.grab().save(str(tmp_path / 'structures_compact.png'))
    page.close()
