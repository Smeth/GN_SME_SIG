"""L'ouverture seule ne doit pas lancer le calcul coûteux de la liste."""

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
def test_open_requires_explicit_acceptance(monkeypatch, accepted):
    view = _view()
    confirm = MagicMock(return_value=accepted)
    monkeypatch.setattr(module, "confirm_yes_no", confirm)
    view.on_page_activated()
    assert confirm.call_args.kwargs["default_no"] is True
    if accepted:
        view._do_refresh.assert_called_once_with(skip_confirm=True)
        view._show_idle_state.assert_not_called()
    else:
        view._do_refresh.assert_not_called()
        view._show_idle_state.assert_called_once_with()
    assert view._load_prompt_open is False


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


def test_reentrant_activation_does_not_duplicate_prompt(monkeypatch):
    view = _view()
    def answer(*args, **kwargs):
        view.on_page_activated()
        return False
    confirm = MagicMock(side_effect=answer)
    monkeypatch.setattr(module, "confirm_yes_no", confirm)
    view.on_page_activated()
    assert confirm.call_count == 1
    view._do_refresh.assert_not_called()
