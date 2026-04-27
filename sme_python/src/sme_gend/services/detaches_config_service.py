"""Chargement / enregistrement de `data/detaches_config.json`."""

from __future__ import annotations

import json
from pathlib import Path

from sme_gend.config.settings import Settings, detaches_config_path
from sme_gend.models.detaches_file_config import DetachesFileConfig


class DetachesConfigService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings

    def path(self) -> Path:
        return detaches_config_path()

    def load(self) -> DetachesFileConfig:
        p = self.path()
        if not p.is_file():
            cfg = DetachesFileConfig()
            self.save(cfg)
            return cfg
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return DetachesFileConfig()
        return DetachesFileConfig.from_dict(data)

    def save(self, cfg: DetachesFileConfig) -> None:
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg.to_dict(), f, ensure_ascii=False, indent=2)
            f.write("\n")
