"""Tests migration config v2 (sans MySQL réel)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from sme_gend.services.config_migration_v2 import migration_v2_done, run_migration_v2


def test_migration_v2_skips_when_done():
    settings = MagicMock()
    repo = MagicMock()
    repo.has_key.return_value = True
    with patch("sme_gend.services.config_migration_v2.AppConfigRepository", return_value=repo):
        assert migration_v2_done(settings) is True
        assert run_migration_v2(settings) == {}
