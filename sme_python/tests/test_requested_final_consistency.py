from types import SimpleNamespace
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QListWidget, QListWidgetItem, QLineEdit
from sme_gend.ui.check_all import bind_list_check_all


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('view_name', ['missions', 'conges', 'desertion'])
def test_visible_rows_toggle_label_and_preserve_other_checks(app, view_name):
    from sme_gend.ui.views.missions_view import MissionsView
    from sme_gend.ui.views.conges_view import CongesView
    from sme_gend.ui.views.desertion_view import DesertionView
    cls = {'missions': MissionsView, 'conges': CongesView, 'desertion': DesertionView}[view_name]
    view = SimpleNamespace(_btn_select_all=QPushButton(), _lbl_checked=QLabel(), _checked={'hidden'},
                           _rows=['A', 'B'], _display_rows=['A', 'B'], _can_edit=True,
                           _btn_end=QPushButton(), _btn_present=QPushButton(), _btn_reset=QPushButton(),
                           _btn_del=QPushButton(), _row_key=lambda row: row, _movement_key=lambda row: row)
    view._fill_table = lambda: cls._update_checked_label(view)
    cls._update_checked_label(view)
    assert view._btn_select_all.text() == 'Tout cocher'
    cls._toggle_select_all_visible(view)
    assert view._btn_select_all.text() == 'Tout décocher'
    assert view._checked == {'hidden', 'A', 'B'}
    cls._toggle_select_all_visible(view)
    assert view._btn_select_all.text() == 'Tout cocher' and view._checked == {'hidden'}
    view._checked.add('A')
    view._rows = view._display_rows = ['A']
    cls._update_checked_label(view)
    assert view._btn_select_all.text() == 'Tout décocher'
    view._rows = view._display_rows = []
    cls._update_checked_label(view)
    assert view._btn_select_all.text() == 'Tout cocher'


def test_list_toggle_tracks_manual_checks_and_filter(app):
    widget = QListWidget()
    for name in ['A', 'B']:
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        widget.addItem(item)
    search = QLineEdit()
    search.textChanged.connect(lambda text: widget.item(1).setHidden(bool(text)))
    button = QPushButton()
    bind_list_check_all(button, widget, search)
    button.click()
    assert button.text() == 'Tout décocher'
    widget.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert button.text() == 'Tout cocher'
    search.setText('A')
    button.click()
    assert button.text() == 'Tout décocher'
    button.click()
    assert widget.item(1).checkState() == Qt.CheckState.Checked


def test_internal_session_from_bulk_and_personnel_is_optional(app):
    from sme_gend.ui.padmin_satellite_dialog import BulkMissionStageDialog
    from sme_gend.ui.agent_stage_edit_dialog import AgentStageEditDialog
    from sme_gend.models.agent_evenement import AgentStageRow
    dialog = BulkMissionStageDialog(None, count=3)
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData('stage_interne'))
    dialog.show(); app.processEvents()
    assert dialog._ssession.isVisible()
    dialog._ssession.setText('Session texte 2026–B')
    dialog._sn.setEditText('FORMATION TEST')
    dialog._on_ok()
    assert dialog._result[1].stage.session == 'Session texte 2026–B'
    dialog.close()
    stage = AgentStageRow('INTERNE', None, None, None, None, None, None, session='Session texte 2026–B')
    edit = AgentStageEditDialog(None, stage=stage)
    assert edit.values()['session'] == stage.session
    edit._session.clear()
    assert edit.values()['session'] is None
    from sme_gend.ui.stage_start_dialog import StageStartDialog
    start = StageStartDialog(None, agent_label='TEST', stage_label='Stage interne', with_fiche=False)
    start.show(); app.processEvents()
    assert start._session.isVisible()
    start._session.setText('SESSION SEULE')
    assert start.stage_detail().session == 'SESSION SEULE'
    start.close()


def test_desertion_reference_optional_and_reopened(app):
    from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog
    from sme_gend.models.agent_evenement import AgentDesertionRow
    dialog = AgentDesertionEditDialog(None, desertion=AgentDesertionRow('2026-09-05', '2026-09', reference_dossier='NOTE/99'))
    assert dialog.values()['reference_dossier'] == 'NOTE/99'
    dialog._reference.clear()
    assert dialog.values()['reference_dossier'] is None


def test_temporal_rows_use_primary_key_even_when_columns_are_reordered():
    from unittest.mock import Mock
    from sme_gend.services.temporal_events import _table_rows
    cursor = Mock()
    cursor.description = [('session',), ('id',), ('agent_id',)]
    cursor.fetchall.return_value = [('SESSION TEXTE', 42, 1)]
    assert _table_rows(cursor, 'parcours_stage', 1) == {42: {'session': 'SESSION TEXTE', 'id': 42, 'agent_id': 1}}


def test_internal_session_alone_requires_nature_before_saving(app, monkeypatch):
    from unittest.mock import Mock
    from sme_gend.ui.views.stages_view import StagesView, _editable_fiche_nature_combo
    from sme_gend.ui.widgets.date_input import FrenchDateEdit
    from sme_gend.ui import padmin_satellite_dialog
    from test_edition_sme_editor import _row
    row = _row('1')
    for name in ('warning', 'information', 'critical'):
        monkeypatch.setattr(padmin_satellite_dialog.QMessageBox, name, Mock())
    view = SimpleNamespace(_check_can_edit=lambda **kwargs: True, _current_row=lambda kind: row,
        _form_widgets=lambda kind: (_editable_fiche_nature_combo(), QLineEdit(), FrenchDateEdit(), FrenchDateEdit()),
        _f_interne_reference=QLineEdit(), _f_interne_session=QLineEdit('SESSION SEULE'),
        _details_for=lambda kind: {}, _sat=Mock(), _invalidate_liste_caches=Mock(),
        _refresh_nature_combo=Mock(), _apply_filters=Mock(),
        _view_data=SimpleNamespace(mark_stale=Mock()), _reload_lists_async=Mock())
    StagesView._save_fiche(view, 'interne')
    view._sat.upsert_stage.assert_not_called()
    padmin_satellite_dialog.QMessageBox.warning.assert_called_once()
