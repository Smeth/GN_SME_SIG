"""L'ouverture affiche les seules rubriques, sans confirmation de chargement."""

from unittest.mock import MagicMock

import pytest

from sme_gend.ui.views import liste_present_view as module


def _view():
    view = module.ListePresentView.__new__(module.ListePresentView)
    view._loaded = False
    view._refresh_busy = False
    view._do_refresh = MagicMock()
    view._show_idle_state = MagicMock()
    return view


@pytest.mark.parametrize("accepted", [False, True])
def test_open_loads_lightweight_outline_without_a_confirmation(monkeypatch, accepted):
    view = _view()
    confirm = MagicMock(return_value=accepted)
    monkeypatch.setattr(module, "confirm_yes_no", confirm)
    view.on_page_activated()
    confirm.assert_not_called()
    view._do_refresh.assert_called_once_with(skip_confirm=True)
    view._show_idle_state.assert_not_called()


@pytest.mark.parametrize("loaded,busy", [(True, False), (False, True), (True, True)])
def test_loaded_or_loading_view_does_not_prompt_or_clear_preview(monkeypatch, loaded, busy):
    view = _view()
    view._loaded, view._refresh_busy = loaded, busy
    confirm = MagicMock()
    monkeypatch.setattr(module, "confirm_yes_no", confirm)
    view.on_page_activated()
    confirm.assert_not_called()
    view._do_refresh.assert_not_called()
    view._show_idle_state.assert_not_called()


def test_reentrant_activation_does_not_duplicate_outline_loading(monkeypatch):
    view = _view()
    def load(*args, **kwargs):
        view._refresh_busy = True
        view.on_page_activated()
    view._do_refresh.side_effect = load
    confirm = MagicMock()
    monkeypatch.setattr(module, "confirm_yes_no", confirm)
    view.on_page_activated()
    confirm.assert_not_called()
    view._do_refresh.assert_called_once_with(skip_confirm=True)
