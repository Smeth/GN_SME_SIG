from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from sme_gend.config.settings import Settings
from sme_gend.services import month_fill as service


@pytest.mark.parametrize('source,target', [('2026-09', '2026-09'), ('2026-09', '2026-08'),
                                          ('2026-13', '2027-01'), ('bad', '2026-10')])
def test_invalid_months_are_rejected(source, target):
    with pytest.raises(ValueError):
        service._months(source, target)


def test_permission_denial_never_reads_or_writes_database(monkeypatch):
    monkeypatch.setattr(service, 'can_fill_month', lambda: False)
    connection = MagicMock(side_effect=AssertionError('No database access'))
    monkeypatch.setattr(service, 'MySQLConnection', connection)
    for operation in (lambda: service.list_month_sources(object()),
                      lambda: service.preview_month_fill(object(), '2026-09', '2026-10'),
                      lambda: service.apply_month_fill(object(), None)):
        with pytest.raises(PermissionError):
            operation()
    connection.assert_not_called()


@pytest.mark.parametrize('start,end,target,expected', [
    ('2026-09-01', '2026-09-30', '2026-10', False),
    ('2026-09-01', '2026-10-01', '2026-10', True),
    ('2026-11-01', None, '2026-10', False),
    ('2026-09-01', None, '2026-10', True),
    ('invalid', None, '2026-10', False),
])
def test_only_dates_overlapping_target_qualify(start, end, target, expected):
    assert service._ongoing(dict(date_debut=start, date_fin=end), target) == expected


@pytest.fixture
def app():
    from PySide6.QtGui import QFontDatabase, QFont
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    font = Path('C:/Windows/Fonts/segoeui.ttf')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
        app.setFont(QFont('Segoe UI', 10))
    return app


def plan(settings, **kwargs):
    return replace(service.MonthFillPlan('2026-09', '2026-10', service.FillOptions(),
                   ('127.0.0.1', 3306, 'unused'), 'revision',
                   (('carriere_situation', 12), ('parcours_stage', 0), ('agent_mission', 0),
                    ('agent_conge', 0), ('agent_malade', 0)), 1,
                   ((1, 'SYNTHETIC AGENT', 'Mission'),),
                   ('Une position sera marquée « À vérifier ».',), ()), **kwargs)


def test_dialog_preview_is_required_and_options_invalidate_it(app, monkeypatch, tmp_path):
    from sme_gend.ui import month_fill_dialog as ui
    from sme_gend.ui.theme import apply_app_theme
    apply_app_theme(app, 'light')
    settings = Settings(database_path=Path('unused'), mysql_view_mois='2026-09')
    expected = plan(settings)
    preview = MagicMock(return_value=expected)
    monkeypatch.setattr(service, 'preview_month_fill', preview)
    monkeypatch.setattr(ui, 'run_with_progress', lambda p, t, label, work: work())
    dialog = ui.MonthFillDialog(None, settings, [('2026-09', 13), ('2026-08', 10)])
    assert not dialog._apply_button.isEnabled()
    assert dialog._target.date().toString('yyyy-MM') == '2026-10'
    assert not any(box.isChecked() for box in dialog._options.values())
    dialog._load_preview()
    assert dialog._apply_button.isEnabled()
    assert 'SYNTHETIC AGENT' in dialog._preview.toPlainText()
    dialog.show()
    app.processEvents()
    assert dialog.grab().save(str(tmp_path / 'month_fill.png'))
    print('Month fill render:', tmp_path / 'month_fill.png')
    dialog._options['stages'].setChecked(True)
    assert dialog._plan is None and not dialog._apply_button.isEnabled()
    dialog._load_preview()
    assert preview.call_args.args[3].stages is True
    dialog._target.setDate(dialog._target.date().addMonths(1))
    assert dialog._plan is None
    dialog.close()


@pytest.mark.parametrize('confirmed,conflicts', [(False, ()), (True, ()), (True, ('Mois occupé',))])
def test_dialog_writes_only_after_preview_and_confirmation(app, monkeypatch, confirmed, conflicts):
    from sme_gend.ui import month_fill_dialog as ui
    settings = Settings(database_path=Path('unused'))
    expected = plan(settings, conflicts=conflicts)
    monkeypatch.setattr(service, 'preview_month_fill', lambda *a: expected)
    apply = MagicMock(return_value=dict(source='2026-09', target='2026-10', review=1, backup='backup.zip'))
    monkeypatch.setattr(service, 'apply_month_fill', apply)
    report = MagicMock()
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_determinate_progress',
                        lambda p, title, total, work: work(report))
    monkeypatch.setattr(ui, 'run_with_progress', lambda p, t, label, work: work())
    monkeypatch.setattr(ui, 'confirm_yes_no', lambda *a, **kw: confirmed)
    monkeypatch.setattr(ui.QMessageBox, 'information', MagicMock())
    dialog = ui.MonthFillDialog(None, settings, [('2026-09', 13)])
    dialog._apply()
    apply.assert_not_called()
    dialog._load_preview()
    dialog._apply()
    if confirmed and not conflicts:
        apply.assert_called_once_with(settings, expected, progress=report)
        assert dialog.result_data['target'] == '2026-10'
    else:
        apply.assert_not_called()
    dialog.close()


def test_cycle_button_uses_same_dialog_and_refreshes_only_after_success(app, monkeypatch):
    from sme_gend.ui.views.sme_workflow_panel import SmeWorkflowPanel
    from sme_gend.ui import month_fill_dialog as ui
    settings = Settings(database_path=Path('unused'))
    monkeypatch.setattr(service, 'can_fill_month', lambda: True)
    completed = MagicMock()
    panel = SmeWorkflowPanel(settings, on_after_workflow=completed)
    opener = MagicMock(return_value=False)
    monkeypatch.setattr(ui, 'run_month_fill_dialog', opener)
    panel._btn_sme_fill.click()
    opener.assert_called_once_with(panel, settings)
    completed.assert_not_called()
    opener.return_value = True
    panel._btn_sme_fill.click()
    completed.assert_called_once()
    panel.close()


def test_file_menu_routes_to_same_workflow_handler(app, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMainWindow
    from sme_gend.ui import shell_window as ui
    window = QMainWindow()
    bar = window.menuBar()
    # Seul le menu réel est construit ; aucune page ni connexion n'est démarrée.
    window._nav_labels = {i: str(i) for i in range(100)}
    window._nav_menu_actions, window._nav_group_menus = {}, []
    window._auth_session = None
    for name in ('_open_paths_config', '_on_empty_month_menu', '_on_rebuild_dossiers_menu',
                 '_on_reconcile_carriere_menu', '_go_page', '_open_global_search',
                 '_reload_current_view', '_refresh_shared_data_now', '_toggle_sidebar', '_apply_theme', '_about'):
        setattr(window, name, MagicMock())
    handler = MagicMock()
    window._pilotage_view = SimpleNamespace(_sme_panel=SimpleNamespace(_on_fill_month_dialog=handler))
    monkeypatch.setattr(ui, 'default_nav_groups', lambda: [])
    monkeypatch.setattr(ui, 'load_sme_context', lambda: SimpleNamespace(theme='light'))
    monkeypatch.setattr('sme_gend.services.auth_context.get_app_auth', lambda: None)
    ui.ShellWindow._build_menu_bar(window)
    top_actions = bar.actions()
    file_action = next(a for a in top_actions if a.text() == '&Fichier')
    file_menu = file_action.menu()
    file_actions = file_menu.actions()
    months_action = next(a for a in file_actions if a.text() == 'Gestion des mois')
    months_menu = months_action.menu()
    action = next(a for a in months_menu.actions() if a.text().startswith('Remplir un mois'))
    action.trigger()
    handler.assert_called_once()
    window.close()
