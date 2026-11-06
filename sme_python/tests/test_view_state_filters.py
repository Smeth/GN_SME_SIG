"""Persistance view_state — filtres liste session-only."""

from __future__ import annotations

from sme_gend.config.view_state import clear_view_state_for_view


def test_clear_view_state_for_view_removes_prefixed_keys(monkeypatch) -> None:
    store: dict = {
        "liste_generale": {"search": "MOUINI"},
        "liste_generale::2026-05": {"search": "test"},
        "dashboard_checklist": {"items": {}},
        "dashboard_checklist::2026-05": {"items": {"a": True}},
    }

    def _load() -> dict:
        return dict(store)

    def _save(data: dict) -> None:
        store.clear()
        store.update(data)

    import sme_gend.config.view_state as vs

    def mutate(change):
        _save(change(_load()))
    monkeypatch.setattr(vs,"_mutate_view_state",mutate)

    n = clear_view_state_for_view("liste_generale")
    assert n == 2
    assert "liste_generale" not in store
    assert "liste_generale::2026-05" not in store
    assert "dashboard_checklist" in store
