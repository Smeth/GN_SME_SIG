from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit
from PySide6.QtCore import Qt

from test_edition_sme_editor import _row


@pytest.fixture
def app():
    from PySide6.QtGui import QFontDatabase, QFont
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    application.setFont(QFont('Segoe UI', 10))
    return application


def test_edition_grade_filter_matches_reference_label_and_code(app):
    from sme_gend.ui.views.edition_sme_view import _EditionModel, _EditionFilterProxy
    model = _EditionModel()
    grades = [SimpleNamespace(code='MDL', libelle='Maréchal des logis', ordre_tri=1)]
    model.load([_row('1', grade='Maréchal des logis'), _row('2', grade='MDL'), _row('3', grade='ADC')],
               None, ['MDL', 'ADC'], [], [], [], grade_rows=grades)
    proxy = _EditionFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_grade('MDL')
    assert proxy.rowCount() == 2
    proxy.set_grade('ADC')
    assert proxy.rowCount() == 1
    proxy.set_grade('')
    assert proxy.rowCount() == 3


def test_mission_nature_combines_with_search_and_history(app):
    from sme_gend.ui.views.missions_view import MissionsView
    from sme_gend.models.numerique_satellite import MissionDetail
    view = SimpleNamespace(_filter_nature=QComboBox(), _rows_all=[_row('1'), _row('2')],
        _rows_history=[_row('3')], _search=QLineEdit(), _chk_history=QCheckBox(),
        _fill_table=Mock(), _update_summary=Mock(),
        _details={'1': MissionDetail('Escorte', None, None, None),
                  '2': MissionDetail('Renfort', None, None, None),
                  '3': MissionDetail('ESCORTE', None, None, None)})
    MissionsView._apply_filters(view)
    view._filter_nature.setCurrentIndex(view._filter_nature.findData('escorte'))
    MissionsView._apply_filters(view)
    assert [r.id for r in view._rows] == ['1']
    view._chk_history.setChecked(True)
    MissionsView._apply_filters(view)
    assert [r.id for r in view._rows] == ['1', '3']
    view._search.setText('Agent 3')
    MissionsView._apply_filters(view)
    assert [r.id for r in view._rows] == ['3']


def test_structure_note_is_recorded_in_affectation_and_scope_is_reset(monkeypatch):
    from test_agent_dossier_manual_sync import _FakeDossierRepo, _patch_sync, _row as sync_row
    from sme_gend.services.agent_dossier_sync import affectation_note_scope, sync_dossier_after_personnel_save
    repo = _FakeDossierRepo()
    _patch_sync(monkeypatch, repo)
    with affectation_note_scope('  NOTE/42  '):
        sync_dossier_after_personnel_save(object(), sync_row(), sync_row(structure='U002'))
    assert repo.affectations[-1]['obs'] == 'Saisie applicative\nRéf. note : NOTE/42'
    sync_dossier_after_personnel_save(object(), sync_row(), sync_row(structure='U003'))
    assert repo.affectations[-1]['obs'] == 'Saisie applicative'


def test_backup_month_dialog_shows_only_archive_months_and_requires_selection(app):
    from sme_gend.ui.backup_months_dialog import BackupMonthsDialog
    dialog = BackupMonthsDialog(('2026-08', '2026-09', '2026-08'))
    assert dialog._months.count() == 2
    assert not dialog._ok.isEnabled()
    dialog._months.item(1).setCheckState(Qt.CheckState.Checked)
    assert dialog.selected_months() == ('2026-08',)
    assert dialog._ok.isEnabled()
    dialog.close()


def test_structure_note_and_month_selection_layouts(app, monkeypatch, tmp_path):
    from sme_gend.ui import edition_sme_bulk_pick_dialog as ui
    from sme_gend.ui.backup_months_dialog import BackupMonthsDialog
    from sme_gend.config.settings import Settings
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    monkeypatch.setattr(ui, 'ReferentielService', lambda settings: SimpleNamespace(structures=lambda: []))
    dialog = ui.BulkStructurePickDialog(None, Settings(database_path=Path('unused')), title='Changement de structure',
        hint='Affecter les agents sélectionnés à une structure.', extra_codes=['U001', 'U002'])
    dialog._reference_note.setText('NOTE/42')
    dialog.show()
    app.processEvents()
    assert dialog._reference_note.isVisible()
    assert dialog.grab().save(str(tmp_path / 'structure_note.png'))
    dialog.close()
    months = BackupMonthsDialog(('2026-08', '2026-09', '2026-10'))
    months.show()
    app.processEvents()
    assert months.grab().save(str(tmp_path / 'backup_months.png'))
    months.close()


def test_missions_nature_filter_is_visible(app, monkeypatch, tmp_path):
    from sme_gend.ui.views import missions_view as ui
    from sme_gend.config.settings import Settings
    monkeypatch.setattr(ui, 'ListeGeneraleRepository', lambda settings: Mock())
    monkeypatch.setattr(ui, 'NumeriqueSatelliteRepository', lambda settings: Mock())
    monkeypatch.setattr(ui.MissionsView, 'on_page_activated', lambda self: None)
    view = ui.MissionsView(Settings(database_path=Path('unused')))
    view.resize(1280, 760)
    view.show()
    app.processEvents()
    assert view._filter_nature.isVisible()
    assert view.grab().save(str(tmp_path / 'missions_nature.png'))
    view.close()
