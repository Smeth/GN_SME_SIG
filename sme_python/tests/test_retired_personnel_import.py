"""Removing list import must preserve navigation indices and CSV update rights."""
from pathlib import Path
import ast


def test_retired_import_is_inaccessible_even_without_auth(monkeypatch):
    from sme_gend.services import auth_context
    from sme_gend.ui import access_control, navigation_pages as nav
    monkeypatch.setattr(auth_context, 'get_app_auth', lambda: None)
    assert not access_control.session_can_access_page(nav.PAGE_CSV_IMPORT)
    assert access_control.session_can_access_page(nav.PAGE_EDITION_SME)


def test_sidebar_omits_retired_page_and_shell_keeps_slot():
    from sme_gend.ui import sidebar_navigation
    from sme_gend.ui.navigation_pages import PAGE_COUNT, PAGE_CSV_IMPORT
    import inspect
    assert 'PAGE_CSV_IMPORT' not in inspect.getsource(sidebar_navigation)
    source = Path(inspect.getfile(sidebar_navigation)).with_name('shell_window.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    calls = sorted((n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id in {'_add_page', '_add_lazy'}
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)), key=lambda n: n.lineno)
    assert len(calls) == PAGE_COUNT
    assert calls[PAGE_CSV_IMPORT].args[0].value == ''
    assert 'CsvImportView' not in source
    assert 'csv_excel_import_service' not in source


def test_existing_csv_update_permission_has_accurate_caption():
    from sme_gend.config.ui_labels import display_ui_title
    caption = display_ui_title('Importer une liste de personnel (CSV / Excel)')
    assert 'CSV' in caption and 'SME' in caption
    assert 'liste de personnel' not in caption
