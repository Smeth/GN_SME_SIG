"""Tests lancement au démarrage Windows."""

from __future__ import annotations

import sys

from sme_gend.services import windows_startup as ws


def test_startup_command_contains_sme_gend_or_exe() -> None:
    cmd = ws.startup_command()
    assert cmd
    if getattr(sys, "frozen", False):
        assert cmd.endswith('"') or ".exe" in cmd.lower()
    else:
        assert "sme_gend" in cmd


def test_is_supported_matches_platform() -> None:
    assert ws.is_supported() == (sys.platform == "win32")
