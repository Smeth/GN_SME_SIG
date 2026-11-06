"""Catalogue validation, deferred media, shared presence and responsive Qt views."""
from pathlib import Path
from time import sleep
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.services.reference_policy import grade_values, position_values


@pytest.mark.parametrize('field,value', [('precedence', 'abc'), ('precedence', -1),
    ('workforce', -1), ('workforce', '1.5'), ('workforce', True), ('workforce', 2147483648)])
def test_grade_invalid_numbers_are_rejected_before_sql(field, value):
    from sme_gend.repositories.mysql_bundle_repositories import MySQLGradeRepository
    repo = MySQLGradeRepository.__new__(MySQLGradeRepository)
    repo._connect = MagicMock(side_effect=AssertionError('SQL must not be reached'))
    args = ['G', 'Grade', 1, 2]
    args[2 if field == 'precedence' else 3] = value
    with pytest.raises(ValueError):
        repo.insert(*args)
    repo._connect.assert_not_called()


@pytest.mark.parametrize('label,present,order', [('Mission', True, 1), ('Présent', False, 1),
    ('StageInterne', True, 1), ('Personnel spécial', True, 'abc'), ('Personnel spécial', True, -1), ('', False, 1)])
def test_invalid_positions_are_rejected(label, present, order):
    with pytest.raises(ValueError):
        position_values(label, present, order)


def test_optional_numbers_and_system_identity():
    assert grade_values(' G ', ' Grade ', '', '0002') == ('G', 'Grade', None, 2)
    with pytest.raises(ValueError, match='code métier'):
        position_values('Mission spéciale', False, 1, old_label='Mission')
    assert position_values('Nouveau service', True, 2, old_label='Ancien service')[0] == 'Nouveau service'


@pytest.mark.parametrize('label,kind,rubrique', [('En mission', 'mission', 'mission'),
    ('Congé maladie', 'malade', 'malade'), ('Stage extérieur', 'stage_ex', 'stage_ex')])
def test_seed_labels_have_stable_business_codes(label, kind, rubrique):
    from sme_gend.services.padmin_satellite import satellite_kind
    from sme_gend.services.numerique_padmin import row_predicate_for_rubrique
    from sme_gend.services.reference_policy import system_position, fixed_presence
    assert satellite_kind(label) == kind
    assert row_predicate_for_rubrique(rubrique)(SimpleNamespace(position_admin=label))
    assert system_position(label) and fixed_presence(label) is False


def test_presence_exports_use_same_cached_index(monkeypatch):
    from sme_gend import padmin_present as presence
    from sme_gend.services.numerique_padmin import is_present_strict, row_predicate_for_rubrique
    index = presence.PadminPresenceIndex.from_rows([PadminRefRow('Service spécial', True, 1)])
    reads = []
    monkeypatch.setattr(presence, '_default_index', (0, None))
    monkeypatch.setattr(presence, 'load_padmin_presence_index', lambda settings: reads.append(settings) or index)
    monkeypatch.setattr('sme_gend.config.get_settings', lambda: object())
    row = SimpleNamespace(position_admin='Service spécial')
    for _ in range(20):
        assert is_present_strict(row)
        assert row_predicate_for_rubrique('presents')(row)
    assert len(reads) == 1
    assert not presence.padmin_counts_as_present('Mission', index=index)


@pytest.fixture
def qt(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QFont, QFontDatabase
    from sme_gend.ui.theme import apply_app_theme
    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
    app.setFont(QFont('Segoe UI', 10))
    apply_app_theme(app, 'light')
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: None)
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: errors.append(a[-1]))
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a: errors.append(a[-1]))
    yield app, errors
    app.processEvents()


def test_dialog_validation_preserves_invalid_draft(qt):
    from sme_gend.ui.views.grade_referentiel_view import GradeEditDialog
    from sme_gend.ui.views.referentiel_views import _PadminDialog
    app, errors = qt
    grade = GradeEditDialog(None)
    grade._code.setText('G')
    grade._libelle.setText('Grade')
    grade._pref.setText('abc')
    grade._validate()
    assert grade.result() == 0 and grade._pref.text() == 'abc' and errors
    position = _PadminDialog(None)
    position._libelle.setText('Service spécial')
    position._ordre.setText('abc')
    position._validate()
    assert position.result() == 0 and position._ordre.text() == 'abc'
    fixed = _PadminDialog(None, PadminRefRow('Mission', True, 1))
    assert fixed._libelle.isReadOnly()
    assert not fixed._present.isEnabled() and not fixed._present.isChecked()
    for dialog in (grade, position, fixed):
        dialog.close()


def test_cancelled_image_selection_never_publishes_media(qt, monkeypatch, tmp_path):
    from PySide6.QtGui import QImage, QColor
    from sme_gend.ui.views import grade_referentiel_view as ui
    app, errors = qt
    source = tmp_path / 'selected.png'
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor('blue'))
    assert image.save(str(source))
    monkeypatch.setattr(ui.QFileDialog, 'getOpenFileName', lambda *a: (str(source), ''))
    published = MagicMock()
    monkeypatch.setattr('sme_gend.services.grade_media.staged_insignia', published)
    dialog = ui.GradeEditDialog(None)
    dialog._code.setText('G')
    dialog._libelle.setText('Grade')
    dialog._pick_insignia()
    assert dialog._pending_insignia == source
    dialog.reject()
    published.assert_not_called()
    assert not errors


def _wait(view):
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest
    timer = QElapsedTimer()
    timer.start()
    while view._reload_busy and timer.elapsed() < 5000:
        QTest.qWait(10)
    assert not view._reload_busy


def test_catalogue_workers_cache_images_preserve_selection_and_render(qt, monkeypatch, tmp_path):
    from PySide6.QtCore import QThread, Qt
    from PySide6.QtGui import QImage, QColor
    from sme_gend.ui.views import grade_referentiel_view as gu
    from sme_gend.ui.views.referentiel_views import PadminView
    app, errors = qt
    grades = [GradeRefRow('G1', 'Grade un', 1, 20), GradeRefRow('G2', 'Grade deux', 2, 30)]
    positions = [PadminRefRow('Mission', False, 1), PadminRefRow('Service spécial', True, 2)]
    threads = []
    def read(value):
        threads.append(QThread.currentThread() == app.thread())
        sleep(0.02)
        return value
    service = SimpleNamespace(settings=object(), grades=lambda: read(grades),
        grade_usage_counts=lambda: read({'G1': 3, 'G2': 4}),
        padmins=lambda: read(positions), padmin_usage_counts=lambda: read({'Mission': 2}))
    image = QImage(24, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor('blue'))
    source = tmp_path / 'insignia.png'
    assert image.save(str(source))
    def lookup(*a):
        threads.append(QThread.currentThread() == app.thread())
        return source
    monkeypatch.setattr(gu, 'grade_insignia_path', lookup)
    grade, position = gu.GradesView(service), PadminView(service)
    grade.reload()
    position.reload()
    _wait(grade)
    _wait(position)
    assert threads and not any(threads) and not errors
    grade._table.selectRow(1)
    position._table.selectRow(1)
    grade.reload()
    position.reload()
    _wait(grade)
    _wait(position)
    assert grade._selected_grade().code == 'G2'
    assert position._current().libelle == 'Service spécial'
    monkeypatch.setattr(gu, 'grade_insignia_path', lambda *a: pytest.fail('No file IO while painting'))
    assert grade._model.data(grade._model.index(0, 0), Qt.ItemDataRole.DecorationRole) is not None
    for view, name in ((grade, 'grades'), (position, 'positions')):
        for width, height in ((960, 720), (1366, 768)):
            view.resize(width, height)
            view.show()
            app.processEvents()
            assert view.width() <= width and view.height() <= height
            assert view.grab().save(str(tmp_path / f'{name}_{width}.png'))
        view.hide()
    position._table.selectRow(0)
    assert not position._btn_del.isEnabled()
    position._search_padmin.setText('introuvable')
    assert not position._btn_edit.isEnabled() and not position._btn_del.isEnabled()
    grade.close()
    position.close()


def test_reference_write_runs_outside_gui(qt):
    from PySide6.QtCore import QThread, QTimer
    from sme_gend.ui.reference_tasks import execute_reference_change
    app, errors = qt
    events, threads = [], []
    timer = QTimer()
    timer.timeout.connect(lambda: events.append(True))
    timer.start(10)
    def write():
        threads.append(QThread.currentThread() == app.thread())
        sleep(0.12)
    assert execute_reference_change(None, write)
    timer.stop()
    assert threads == [False] and len(events) > 2 and not errors
