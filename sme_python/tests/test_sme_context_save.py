"""Sauvegarde contexte SME — ne pas rouvrir sme_periode pour les préférences UI seules."""

from __future__ import annotations

from sme_gend.config.sme_context import SmeContext, save_sme_context


def test_save_sme_context_skips_operational_when_disabled(monkeypatch) -> None:
    calls: list[str] = []

    class _Ws:
        def get_active_mois(self) -> str:
            return "2026-04"

        def set_active_mois(self, mois: str) -> str:
            calls.append(mois)
            return mois

    monkeypatch.setattr(
        "sme_gend.services.sme_workspace_service.SmeWorkspaceService",
        lambda _s: _Ws(),
    )
    monkeypatch.setattr("sme_gend.config.sme_context._save_local_ui_prefs", lambda _c: None)
    monkeypatch.setattr(
        "sme_gend.services.app_config_store.save_config_dict",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: object())

    ctx = SmeContext(mois_actif="2026-03", hidden_columns=["x"])
    save_sme_context(ctx, promote_operational_month=False, save_shared_preferences=False)
    assert calls == []
