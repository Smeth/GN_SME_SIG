"""Config centralisée — chargement MySQL sans repli fichier."""

from __future__ import annotations

from sme_gend.services.app_config_store import KEY_DETACHES, load_config_model


def test_load_config_model_creates_default(monkeypatch):
    store: dict[str, dict] = {}

    def _load(_settings, key):
        return store.get(key)

    def _save(_settings, key, payload, **kwargs):
        store[key] = payload.to_dict()

    monkeypatch.setattr(
        "sme_gend.services.shared_config_write.load_shared_payload",
        _load,
    )
    monkeypatch.setattr(
        "sme_gend.services.shared_config_write.save_shared_model",
        _save,
    )

    class _Cfg:
        def __init__(self, d: dict):
            self.value = d.get("x", 0)

        def to_dict(self):
            return {"x": self.value}

    from sme_gend.config.settings import Settings
    from pathlib import Path
    cfg = load_config_model(Settings(database_path=Path("unused.mdb")), KEY_DETACHES, default_factory=_Cfg)
    assert cfg.value == 0
    assert KEY_DETACHES in store


def test_load_config_model_reads_existing(monkeypatch):
    store = {KEY_DETACHES: {"x": 42}}

    monkeypatch.setattr(
        "sme_gend.services.shared_config_write.load_shared_payload",
        lambda _s, key: store.get(key),
    )
    monkeypatch.setattr(
        "sme_gend.services.shared_config_write.save_shared_model",
        lambda *_a, **_k: None,
    )

    class _Cfg:
        def __init__(self, d: dict):
            self.value = d.get("x", 0)

        def to_dict(self):
            return {"x": self.value}

    from sme_gend.config.settings import Settings
    from pathlib import Path
    cfg = load_config_model(Settings(database_path=Path("unused.mdb")), KEY_DETACHES, default_factory=_Cfg)
    assert cfg.value == 42
