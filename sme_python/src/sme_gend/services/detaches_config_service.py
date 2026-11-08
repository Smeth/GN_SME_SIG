"""Chargement / enregistrement de la config détachés (MySQL app_config)."""

from __future__ import annotations

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
        from sme_gend.services.app_config_store import KEY_DETACHES, load_config_model

        def _factory(d: dict) -> DetachesFileConfig:
            return DetachesFileConfig.from_dict(d) if d else DetachesFileConfig()

        return load_config_model(self._settings, KEY_DETACHES, default_factory=_factory)

    def load_for_export(self) -> DetachesFileConfig:
        """Read optional presentation settings without initialization or writes.

        A legacy database may not have shared settings yet. Its absent layout
        uses an in-memory default; an unreadable existing configuration remains
        an error rather than silently changing the exported presentation.
        """
        from sme_gend.repositories.app_config_repository import AppConfigRepository
        from sme_gend.services.app_config_store import KEY_DETACHES
        from sme_gend.services.shared_config_write import load_shared_payload

        if not AppConfigRepository(self._settings).table_exists('app_config'):
            return DetachesFileConfig()
        raw = load_shared_payload(self._settings, KEY_DETACHES)
        return DetachesFileConfig.from_dict(raw) if raw is not None else DetachesFileConfig()

    def save(self, cfg: DetachesFileConfig) -> None:
        from sme_gend.services.app_config_store import KEY_DETACHES, save_config_model

        save_config_model(self._settings, KEY_DETACHES, cfg)
