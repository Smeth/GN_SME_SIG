"""Dossier preparation: fresh shared cache, responsive UI, preserved edit guards."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import time

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QApplication, QDialog
from sme_gend.config.settings import Settings
from sme_gend.services import career_alerts_cache as cache
from test_personnel_dossier_open import _row


@pytest.fixture
def cached_engine(monkeypatch):
    cache.invalidate_career_alerts_cache()
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    revision = ['A']
    monkeypatch.setattr(cache, '_cache_key', lambda st: (st, st.mysql_view_mois))
    monkeypatch.setattr('sme_gend.services.data_sync.read_data_revision', lambda _: revision[0])
    scan = MagicMock(return_value=[SimpleNamespace(agent_legacy_id='7', severity='warning'),
                                  SimpleNamespace(agent_legacy_id='8', severity='warning')])
    monkeypatch.setattr(cache, 'scan_all_career_alerts', scan)
    yield settings, revision, scan
    cache.invalidate_career_alerts_cache()


def test_dossier_alert_cache_is_shared_across_agents_but_not_revisions_or_months(cached_engine):
    from dataclasses import replace
    settings, revision, scan = cached_engine
    assert len(cache.dossier_alerts_for_agent(settings, '7')) == 1
    assert len(cache.dossier_alerts_for_agent(settings, '8')) == 1
    assert scan.call_count == 1
    revision[0] = 'B'
    cache.dossier_alerts_for_agent(settings, '7')
    assert scan.call_count == 2
    cache.dossier_alerts_for_agent(replace(settings, mysql_view_mois='2026-08'), '7')
    assert scan.call_count == 3


def test_dossier_alert_cache_does_not_publish_a_scan_crossing_a_commit(cached_engine):
    settings, revision, scan = cached_engine
    def changed(*_, **__):
        revision[0] = 'B'
        return [SimpleNamespace(agent_legacy_id='7', severity='warning')]
    scan.side_effect = changed
    assert len(cache.dossier_alerts_for_agent(settings, '7')) == 1
    assert scan.call_count == 2


def test_open_prepares_sql_and_audit_off_the_gui_thread_and_rechecks_permission(monkeypatch):
    from sme_gend.ui import personnel_dossier_open as opening
    app = QApplication.instance() or QApplication([])
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    row = _row()
    calls = []
    heartbeats = []
    permitted = [True]
    def fresh(_):
        calls.append(('read', QThread.currentThread() != app.thread()))
        time.sleep(.06)
        return row
    service = SimpleNamespace(settings=settings, get_by_id=fresh)
    monkeypatch.setattr(opening, 'PersonnelService', lambda _: service)
    def snapshot(*_, **kwargs):
        assert kwargs == {'include_alerts': False}
        return {}
    monkeypatch.setattr('sme_gend.services.dossier_read_snapshot.load_dossier_read_snapshot', snapshot)
    monkeypatch.setattr('sme_gend.services.data_sync.read_data_revision', lambda _: 'A')
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.evaluate_editing_state_for_settings', lambda _: SimpleNamespace(locked=False))
    monkeypatch.setattr('sme_gend.services.activity_audit_log.log_dossier_opened',
                        lambda *_: calls.append(('audit', QThread.currentThread() != app.thread())))
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda _: permitted[0])
    monkeypatch.setattr('sme_gend.services.auth_guard.session_can_modify_personnel', lambda: False)
    class Dialog(QDialog):
        liste_generale_changed = False
        def __init__(self, parent, row, ref, svc, **kwargs):
            super().__init__(parent)
            assert QThread.currentThread() == app.thread()
            assert kwargs['allow_edit'] is False
            assert kwargs['initial_snapshot'] == {}
        def prepare_for_exec(self):
            pass
        def exec(self):
            return QDialog.DialogCode.Accepted
    monkeypatch.setattr(opening, 'PersonnelDossierDialog', Dialog)
    timer = QTimer()
    timer.timeout.connect(lambda: heartbeats.append(True))
    timer.start(5)
    try:
        result = opening.open_personnel_dossier_dialog(None, row, object(), service)
    finally:
        timer.stop()
    assert result.code == QDialog.DialogCode.Accepted
    assert calls == [('read', True), ('audit', True)]
    assert heartbeats
    permitted[0] = False
    refused = opening.open_personnel_dossier_dialog(None, row, object(), service)
    assert refused.code == QDialog.DialogCode.Rejected


def test_prepared_editable_dialog_constructs_without_sql(monkeypatch, tmp_path):
    from sme_gend.ui import personnel_dossier_dialog as ui
    app = QApplication.instance() or QApplication([])
    settings = Settings(database_path=Path('unused'))
    monkeypatch.setattr('pymysql.connect', MagicMock(side_effect=AssertionError('SQL on GUI thread')))
    monkeypatch.setattr(ui, 'AgentDossierEventsService', lambda _: SimpleNamespace())
    monkeypatch.setattr(ui, 'AgentDossierService', lambda _: SimpleNamespace())
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_admin_role', lambda: False)
    snapshot = dict(career_snapshots=[], events={k: [] for k in ('missions','stages','malades','desertions','conges')},
                    dossier={k: [] for k in ('list_diplomes','list_distinctions','list_passations','list_affectations')},
                    grades=[], structures=[], media=dict(root=tmp_path, photo=None, grade=None),
                    positions=[], timeline=[], situation=None, alerts=[])
    ref = SimpleNamespace(grades=lambda: [], structures=lambda: [])
    dialog = ui.PersonnelDossierDialog(None, _row(), ref, SimpleNamespace(settings=settings),
                                      allow_edit=True, initial_snapshot=snapshot)
    assert dialog._allow_edit and dialog._readonly_snapshot is None
    assert dialog._ref is ref
    assert dialog._initial_tables_pending is snapshot
    dialog._tabs.setCurrentIndex(1)
    assert dialog._initial_tables_pending is None
    assert dialog._readonly_snapshot is None and dialog._ref is ref
    dialog.deleteLater()
    app.processEvents()
