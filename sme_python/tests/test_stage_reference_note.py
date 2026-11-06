from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from datetime import date

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from sme_gend.models.numerique_satellite import StageDetail
from sme_gend.ui.padmin_satellite_dialog import BulkMissionStageDialog, PadminSatelliteDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('kind', ['stage_local', 'stage_ex', 'stage_interne'])
@pytest.mark.parametrize('reference', [' NOTE/2026/42 ', ''])
def test_bulk_stage_reference_is_visible_optional_and_returned(app, kind, reference):
    dialog = BulkMissionStageDialog(None, count=4)
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData(kind))
    dialog.show()
    app.processEvents()
    assert dialog._sr.isVisible()
    assert any(label.text() == 'Réf/note (facultatif)' for label in dialog.findChildren(QLabel))
    dialog._sr.setText(reference)
    dialog._sn.setEditText('FORMATION TEST')
    dialog._on_ok()
    assert dialog._result[1].stage.reference_dossier == (reference.strip() or None)
    dialog.close()


def test_individual_stage_reopens_reference(app):
    dialog = PadminSatelliteDialog(None, kind='stage_ex', agent_label='TEST',
                                  stage=StageDetail('Formation', 'École', None, reference_dossier='NOTE/42'))
    assert dialog._stage_ref.text() == 'NOTE/42'
    dialog._on_ok()
    assert dialog._result.stage.reference_dossier == 'NOTE/42'


@pytest.mark.parametrize('target', [None, 12])
def test_stage_note_is_written_on_insert_and_update(monkeypatch, target):
    from sme_gend.repositories import mysql_numerique_repository as module
    cur = Mock()
    repo = SimpleNamespace(_agent_id=lambda *_: 1, _periode_id=lambda *_: 2,
                           _connect=lambda: nullcontext(SimpleNamespace(cursor=lambda: nullcontext(cur))))
    monkeypatch.setattr(module, 'checked_target', lambda *args, **kwargs: target)
    from inspect import unwrap
    write = unwrap(module.MySQLNumeriqueRepository.upsert_stage)
    write(repo, '42', StageDetail('Formation', 'École', None, reference_dossier='NOTE/42'),
          type_code='EXTERIEUR', date_debut='2026-11-01')
    query, params = cur.execute.call_args.args
    assert 'reference_dossier' in query
    assert 'NOTE/42' in params


def test_stage_note_is_read_without_changing_write_token(monkeypatch):
    from sme_gend.repositories import temporal_reads
    cur = Mock()
    cur.fetchall.return_value = [('42', 'Formation', 'École', None, date(2026,11,1), None, 'NOTE/42', 'SESSION TEST', 12, 2, 'rev')]
    repo = SimpleNamespace(_settings=object(), _connect=lambda **kwargs: nullcontext(SimpleNamespace(cursor=lambda: nullcontext(cur))))
    monkeypatch.setattr(temporal_reads, 'eligibility', lambda *args, **kwargs: ('1=1', ()))
    result = temporal_reads.load_details(repo, 'stage', type_code='EXTERIEUR')['42']
    assert result.reference_dossier == 'NOTE/42'
    assert result.session == 'SESSION TEST'
    assert result.write_token == (12, 2, 'rev')


def test_personnel_dossier_stage_and_mission_have_optional_reference(app):
    from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog
    from sme_gend.ui.agent_mission_edit_dialog import AgentMissionEditDialog
    from sme_gend.models.agent_evenement import AgentStageRow
    stage = AgentStageRow('LOCAL', None, None, 'Formation', 'École', None, None, reference_dossier='NOTE/42')
    dialog = AgentStageEditDialog(None, stage=stage)
    assert dialog._reference.text() == 'NOTE/42'
    assert dialog.values()['reference_dossier'] == 'NOTE/42'
    dialog._reference.clear()
    assert dialog.values()['reference_dossier'] is None
    mission = AgentMissionEditDialog(None)
    mission._reference.setText('MISSION/42')
    assert mission.values()['reference_dossier'] == 'MISSION/42'


def test_stage_start_returns_note(app):
    from sme_gend.ui.stage_start_dialog import StageStartDialog
    dialog = StageStartDialog(None, agent_label='TEST', stage_label='Stage interne', with_fiche=True)
    dialog._reference.setText('NOTE/42')
    assert dialog.stage_detail().reference_dossier == 'NOTE/42'


def test_stage_end_note_is_independent_of_optional_diploma(app, monkeypatch):
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    def accept(dialog):
        dialog._enable.setChecked(False)
        dialog._reference.setText('FIN/42')
        dialog._on_ok()
        return dialog.DialogCode.Accepted
    monkeypatch.setattr(StageEndDiplomaDialog, 'exec', accept)
    assert StageEndDiplomaDialog.run(None, agent_count=2, default_date_iso='2026-11-05') == {
        '_stage_reference': 'FIN/42', '_stage_end_date': '2026-11-05'}


def test_stage_completion_date_is_required_even_without_diploma(app, monkeypatch):
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from PySide6.QtWidgets import QMessageBox, QDialog
    warning = Mock()
    monkeypatch.setattr(QMessageBox, 'warning', warning)
    dialog = StageEndDiplomaDialog(None, agent_count=1, default_date_iso='2026-09-30')
    dialog._enable.setChecked(False)
    dialog._end_date.set_from_text('')
    dialog._on_ok()
    assert dialog.result() != QDialog.DialogCode.Accepted
    warning.assert_called_once()
    dialog._end_date.set_from_text('17/09/2026')
    dialog._on_ok()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


@pytest.mark.parametrize('method', ['add_stage_for_agent', 'update_stage_for_agent'])
def test_personnel_stage_sql_persists_reference(method):
    from inspect import unwrap
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    cur = Mock(lastrowid=3)
    repo = SimpleNamespace(_agent_id=lambda *_: 1,
                          _connect=lambda: nullcontext(SimpleNamespace(cursor=lambda: nullcontext(cur))))
    args = ('42', 3) if method.startswith('update') else ('42',)
    unwrap(getattr(MySQLNumeriqueRepository, method))(repo, *args, type_code='LOCAL',
            date_debut=None, date_fin=None, nature='TEST', lieu=None, observation=None,
            reference_dossier='NOTE/42')
    query, params = cur.execute.call_args.args
    assert 'reference_dossier' in query and 'NOTE/42' in params
    assert query.count('%s') == len(params)


def test_dossier_does_not_merge_stages_with_different_notes():
    from sme_gend.models.agent_evenement import AgentStageRow
    from sme_gend.services.dossier_grouping import group_stages
    rows = [AgentStageRow('LOCAL', None, None, 'TEST', None, None, month, reference_dossier=ref)
            for month, ref in [('2026-09', 'NOTE/1'), ('2026-10', 'NOTE/2')]]
    assert len(group_stages(rows)) == 2
