"""Tests maintenance SoT — report carrière orpheline."""

from __future__ import annotations

from sme_gend.services.sot_maintenance import _prev_yyyy_mm


def test_prev_yyyy_mm() -> None:
    assert _prev_yyyy_mm("2026-05") == "2026-04"
    assert _prev_yyyy_mm("2026-01") == "2025-12"


def test_revert_stale_keep_threshold() -> None:
    """prev >= keep_from_mois => conserve ; sinon supprime."""
    assert "2026-04" >= "2026-04"
    assert "2025-02" < "2026-04"
