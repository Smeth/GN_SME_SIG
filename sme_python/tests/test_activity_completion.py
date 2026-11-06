"""User-facing date validation and selection retention for activity returns."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox


@pytest.mark.parametrize('value,month,start,part', [
    ('', '2026-09', None, 'obligatoire'),
    ('2099-09-01', '2099-09', None, 'future'),
    ('2026-09-17', '2026-08', None, 'mois consulté'),
    ('2026-09-17', '2026-09', '2026-09-20', 'précéder'),
])
def test_invalid_return_dates_are_refused(value, month, start, part):
    from sme_gend.services.activity_completion import validate_activity_end
    with pytest.raises(ValueError, match=part):
        validate_activity_end(value, month, start)


def test_return_date_is_real_and_may_equal_departure():
    from sme_gend.services.activity_completion import validate_activity_end
    assert validate_activity_end('17/09/2026', '2026-09', '2026-09-17') == date(2026, 9, 17)


def test_activity_return_checks_edit_permission_before_opening_a_transaction(monkeypatch):
    from sme_gend.services.activity_completion import complete_activity
    from sme_gend.services import sensitive_data_policy
    def denied(**kwargs):
        raise PermissionError('REFUSED')
    monkeypatch.setattr(sensitive_data_policy, 'assert_may_edit_personnel', denied)
    with pytest.raises(PermissionError, match='REFUSED'):
        complete_activity(None, None, 'mission', '2026-09-17', expected=None)


def test_return_batch_keeps_failed_keys_and_reports_committed_mirror_failure(monkeypatch):
    from sme_gend.ui import activity_return_action as module
    from sme_gend.services import activity_completion as service
    from sme_gend.repositories.mysql_connection import CommittedMirrorError
    monkeypatch.setattr(module.ActivityReturnDialog, 'run', lambda *a, **kw: date(2026, 9, 17))
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.resolve_liste_view_mois', lambda _: '2026-09')
    result = service.ActivityCompletionResult(1, date(2026, 9, 17), 'Présent')
    calls = iter([result, ValueError('STALE'), CommittedMirrorError('COMMITTED')])
    def complete(*args, **kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(service, 'complete_activity', complete)
    info = Mock()
    monkeypatch.setattr(QMessageBox, 'information', info)
    requests = [(str(i), SimpleNamespace(noms=f'Agent {i}', id=str(i)), None) for i in range(3)]
    assert module.run_activity_return(None, None, 'mission', requests, title='Missions') == {'0', '2'}
    text = info.call_args.args[2]
    assert '2 retour(s) enregistré(s)' in text
    assert 'Agent 1 : STALE' in text and 'lignes conservées cochées' in text
    assert 'confirmé dans MySQL' in text and 'Ne recommencez pas' in text


def test_return_cancel_does_not_write(monkeypatch):
    from sme_gend.ui import activity_return_action as module
    from sme_gend.services import activity_completion as service
    monkeypatch.setattr(module.ActivityReturnDialog, 'run', lambda *a, **kw: None)
    monkeypatch.setattr('sme_gend.config.liste_generale_scope.resolve_liste_view_mois', lambda _: '2026-09')
    complete = Mock()
    monkeypatch.setattr(service, 'complete_activity', complete)
    assert module.run_activity_return(None, None, 'mission', [], title='Missions') is None
    complete.assert_not_called()


def test_return_dialog_requires_a_valid_date(monkeypatch):
    from sme_gend.ui.activity_return_action import ActivityReturnDialog
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    dialog = ActivityReturnDialog(None, title='Retour', agent_count=1, month='2026-09')
    dialog._date.set_from_text('')
    dialog._accept_date()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog._date.set_from_text('17/09/2026')
    dialog._accept_date()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()
    app.processEvents()


@pytest.mark.parametrize('module_name,view_name,method_name,kind', [
    ('missions_view', 'MissionsView', '_end_checked_missions', 'mission'),
    ('conges_view', 'CongesView', '_reset_checked', 'conge'),
    ('desertion_view', 'DesertionView', '_reset_checked', 'desertion'),
])
def test_activity_views_keep_failures_selected(monkeypatch, module_name, view_name, method_name, kind):
    from importlib import import_module
    from sme_gend.ui import activity_return_action
    from sme_gend.models.numerique_satellite import MissionDetail, DeserteurDetail
    from sme_gend.models.liste_generale import ListeGeneraleRow
    values = {name: None for name in ListeGeneraleRow.__dataclass_fields__}
    values.update(id='1', noms='TEST', access_join_id='42')
    row = ListeGeneraleRow(**values)
    second = replace(row, id='2', access_join_id='43')
    detail = MissionDetail('NATURE', None, None, '2026-09-10') if kind == 'mission' else DeserteurDetail('2026-09-10')
    run = Mock(return_value={'42'})
    monkeypatch.setattr(activity_return_action, 'run_activity_return', run)
    monkeypatch.setattr('sme_gend.services.liste_generale_cache.invalidate_liste_cache', Mock())
    view = SimpleNamespace(_checked={'42', '43'}, _check_can_edit=lambda: True,
        _scoped_settings=object(), _rows=[row, second], _details={'42': detail, '43': detail},
        _row_key=lambda row: row.satellite_join_key(), _view_data=SimpleNamespace(mark_stale=Mock()),
        _reload_lists_async=Mock(), _display_rows=[SimpleNamespace(liste=row, conge=detail),
                                                SimpleNamespace(liste=second, conge=detail)],
        _movement_key=lambda movement: movement.liste.satellite_join_key())
    module = import_module('sme_gend.ui.views.' + module_name)
    getattr(getattr(module, view_name), method_name)(view)
    assert view._checked == {'43'}
    assert run.call_args.args[2] == kind
    assert run.call_args.args[3][0] == ('42', row, detail)


@pytest.mark.parametrize('reference,table,source_id,prefix,suffix', [
    ('sgi:auto:agent_mission:13', 'agent_mission', 13, 'sgi:auto:agent_mission:', ''),
    ('sgi:auto:activity_return:agent_desertion:14', 'agent_desertion', 14,
     'sgi:auto:activity_return:agent_desertion:', ''),
    ('sgi:auto:position:15', 'agent_position_event', 15, 'sgi:auto:position:', ''),
    ('sgi:auto:stage_end:16:INTERNE:2026-09-17', 'carriere_situation', 16,
     'sgi:auto:stage_end:', ':INTERNE:2026-09-17'),
])
def test_automatic_position_sources_can_be_remapped(reference, table, source_id, prefix, suffix):
    from sme_gend.services.automatic_position_links import automatic_source
    assert automatic_source(reference) == (table, source_id, prefix, suffix)


@pytest.mark.parametrize('reference', ['sgi:auto:unknown:1', 'sgi:auto:stage_end:1:UNKNOWN:2026-09-17',
    'sgi:auto:activity_return:app_config:1', 'sgi:auto:agent_mission:no-number', 'plain text'])
def test_unknown_automatic_sources_are_refused(reference):
    from sme_gend.services.automatic_position_links import automatic_source
    with pytest.raises(ValueError):
        automatic_source(reference)


def test_position_link_replacement_requires_an_outer_transaction(monkeypatch):
    from sme_gend.services.mysql_import_period import purge_sme_periode_for_reimport
    monkeypatch.setattr('sme_gend.repositories.mysql_connection.has_import_transaction', lambda _: False)
    with pytest.raises(RuntimeError, match='transaction de restauration'):
        purge_sme_periode_for_reimport(object(), '2026-09', _replace_position_links=True)


@pytest.mark.parametrize('kind', ['conge', 'desertion'])
def test_failed_activity_selection_survives_the_real_async_reload(monkeypatch, kind):
    from importlib import import_module
    from sme_gend.ui.view_lifecycle import ViewDataState
    module = import_module('sme_gend.ui.views.' + ('conges_view' if kind == 'conge' else 'desertion_view'))
    cls = module.CongesView if kind == 'conge' else module.DesertionView
    row = SimpleNamespace(id='1', position_admin='Déserteur', satellite_join_key=lambda: '42')
    movement = SimpleNamespace(liste=row)
    pending = []
    monkeypatch.setattr(module, 'run_in_background', lambda owner, work, done, **kw: pending.append(done))
    if kind == 'conge':
        monkeypatch.setattr(module, 'build_conge_movement_rows', lambda *a: [movement])
        monkeypatch.setattr(module, 'build_conge_history_rows', lambda *a: [])
    view = SimpleNamespace(_view_data=ViewDataState(stale=True), _checked={'42', 'gone'},
        _liste=Mock(), _sat=Mock(), _settings=object(), _scoped_settings=object(), _view_mois='2026-09',
        _refresh_action_state=Mock(), _summary=Mock(), _apply_filters=Mock(),
        _rebuild_period_combos=Mock(), _update_summary=Mock(),
        _movement_key=lambda movement: movement.liste.satellite_join_key())
    cls._reload_lists_async(view, preserve_checked=True)
    pending[0](([row], {}, []))
    assert view._checked == {'42'}
