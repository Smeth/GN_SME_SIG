from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtTest import QTest

from sme_gend.config.settings import Settings
from sme_gend.ui.shell_window import ShellWindow


def test_reload_prefers_explicit_database_refresh_to_generic_load(monkeypatch):
    app = QApplication.instance() or QApplication([])
    page = SimpleNamespace(mark_view_stale=Mock(), refresh_from_database=Mock(), load_data=Mock())
    shell = SimpleNamespace(_has_unsaved_changes=lambda: False, _stack=Mock(), _page_widget=lambda *a, **kw: page,
        _settings=Settings(database_path=Path('unused')), _context_banner=Mock(), statusBar=Mock())
    monkeypatch.setattr(QApplication, 'activeModalWidget', lambda: None)
    monkeypatch.setattr('sme_gend.services.sme_editing_policy.peek_editing_state', lambda settings: None)
    ShellWindow._reload_current_view(shell)
    page.refresh_from_database.assert_called_once()
    page.load_data.assert_not_called()


def test_edition_f5_preserves_filters_and_selection():
    from sme_gend.ui.views.edition_sme_view import EditionSmeView
    from sme_gend.services import liste_generale_cache
    settings = Settings(database_path=Path('unused'))
    liste_generale_cache.get_liste_rows(settings, lambda: ['OLD'])
    load = Mock(side_effect=lambda **kwargs: liste_generale_cache.get_liste_rows(settings, lambda: ['NEW']))
    page = SimpleNamespace(_settings=settings, load_data=load)
    page._actualiser = lambda: EditionSmeView._actualiser(page)
    EditionSmeView.refresh_from_database(page)
    load.assert_called_once_with(preserve_ui=True)
    assert liste_generale_cache.get_liste_rows(settings, lambda: ['STALE']) == ['NEW']


def test_f5_key_with_present_list_focus_triggers_single_refresh(monkeypatch):
    from sme_gend.ui.views.liste_present_view import ListePresentView
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ListePresentView, 'on_page_activated', lambda self: None)
    refresh = Mock()
    monkeypatch.setattr(ListePresentView, '_do_refresh', lambda self, **kw: refresh(**kw))
    shell = QMainWindow()
    page = ListePresentView(Mock())
    shell.setCentralWidget(page)
    shortcut = QShortcut(QKeySequence('F5'), shell)
    shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
    ambiguous = Mock()
    shortcut.activated.connect(page.refresh_from_database)
    shortcut.activatedAmbiguously.connect(ambiguous)
    shell.show()
    shell.activateWindow()
    page._search.setFocus()
    app.processEvents()
    QTest.keyClick(page._search, Qt.Key.Key_F5)
    app.processEvents()
    refresh.assert_called_once_with(skip_confirm=True)
    ambiguous.assert_not_called()
    shell.close()
