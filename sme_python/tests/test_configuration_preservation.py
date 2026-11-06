from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem

from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.ui.views import detaches_config_view as module


@pytest.fixture
def editor(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(module, 'confirm_yes_no', lambda *a, **kw: True)
    for name in ('critical', 'warning', 'information'):
        monkeypatch.setattr(QMessageBox, name, MagicMock())
    raw = {
        'version': 1, 'future': {'nested': [1, 2]},
        'options': {'future_option': {'value': 3}},
        'structures': [
            {'sigle': 'ABC', 'ordre': 1, 'titres_lignes': ['A', 'B', 'C', 'D', 'E'], 'future_structure': [4]},
            {'sigle': 'XYZ', 'ordre': 2, 'titres_lignes': ['X'], 'future_structure': [5]},
        ],
    }
    service = MagicMock()
    service.load.return_value = DetachesFileConfig.from_dict(raw)
    view = module.DetachesConfigView(service)
    yield view, service, raw
    view.close()
    app.processEvents()


def test_edit_preserves_all_titles_and_nested_unknown_fields(editor):
    view, service, raw = editor
    assert view._table.columnCount() == 7
    view._table.item(0, 1).setText('RENAMED')
    view._lieu.setText('NEW PLACE')
    view._save()
    saved = service.save.call_args.args[0].to_dict()
    assert saved['future'] == raw['future']
    assert saved['options']['future_option'] == raw['options']['future_option']
    assert saved['options']['lieu_defaut'] == 'NEW PLACE'
    assert saved['structures'][0] == {**raw['structures'][0], 'sigle': 'RENAMED'}
    assert saved['structures'][1] == raw['structures'][1]
    assert raw['structures'][0]['sigle'] == 'ABC'


@pytest.mark.parametrize('column,value', [(1, ''), (0, 'invalid')])
def test_invalid_row_blocks_save_and_keeps_draft(editor, column, value):
    view, service, _ = editor
    view._table.item(0, column).setText(value)
    view._save()
    service.save.assert_not_called()
    assert view._table.item(0, column).text() == value
    assert view.is_dirty()
    assert 'Ligne 1' in QMessageBox.critical.call_args.args[2]


def test_moving_rows_keeps_extra_titles_and_metadata(editor):
    view, _, _ = editor
    view._table.setCurrentCell(0, 1)
    view._move(1)
    rows = view._gather_structures()
    assert rows[1].titres_lignes == ['A', 'B', 'C', 'D', 'E']
    assert rows[1].to_dict()['future_structure'] == [4]
    assert rows[0].to_dict()['future_structure'] == [5]


def test_blank_new_row_is_reported_instead_of_discarded(editor):
    view, service, _ = editor
    view._add_row()
    view._save()
    service.save.assert_not_called()
    assert 'Ligne 3' in QMessageBox.critical.call_args.args[2]


def test_duplicate_sigles_are_compared_after_normalization(editor):
    view, service, _ = editor
    view._table.item(1, 1).setText(' abc ')
    view._save()
    service.save.assert_not_called()
    QMessageBox.warning.assert_called_once()


@pytest.mark.parametrize('model', [SoitTransmisConfig, DetachesFileConfig])
def test_unknown_data_roundtrip_is_deeply_isolated(model):
    raw = {'future': {'nested': [1, 2]}}
    cfg = model.from_dict(raw)
    raw['future']['nested'].append(3)
    assert cfg.to_dict()['future'] == {'nested': [1, 2]}
    encoded = cfg.to_dict()
    encoded['future']['nested'].append(4)
    assert cfg.to_dict()['future'] == {'nested': [1, 2]}
