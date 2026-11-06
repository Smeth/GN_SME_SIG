"""Politique sessions simultanées (config)."""

from __future__ import annotations

from sme_gend.config.auth_config import (
    AuthConfig,
    CONCURRENT_SESSIONS_LIMITED,
    CONCURRENT_SESSIONS_OFF,
    CONCURRENT_SESSIONS_SINGLE,
    clamp_concurrent_sessions_limit,
    normalize_concurrent_sessions_mode,
)
from sme_gend.services.auth_session_registry import concurrent_policy


def test_normalize_concurrent_sessions_mode():
    assert normalize_concurrent_sessions_mode("off") == CONCURRENT_SESSIONS_OFF
    assert normalize_concurrent_sessions_mode("single") == CONCURRENT_SESSIONS_SINGLE
    assert normalize_concurrent_sessions_mode("limited") == CONCURRENT_SESSIONS_LIMITED
    assert normalize_concurrent_sessions_mode("unknown") == CONCURRENT_SESSIONS_OFF


def test_clamp_concurrent_sessions_limit():
    assert clamp_concurrent_sessions_limit(1) == 2
    assert clamp_concurrent_sessions_limit(99) == 20
    assert clamp_concurrent_sessions_limit(5) == 5


def test_concurrent_policy_single():
    cfg = AuthConfig(concurrent_sessions_mode=CONCURRENT_SESSIONS_SINGLE, max_concurrent_sessions=5)
    mode, limit = concurrent_policy(cfg)
    assert mode == CONCURRENT_SESSIONS_SINGLE
    assert limit == 1


def test_concurrent_policy_limited():
    cfg = AuthConfig(concurrent_sessions_mode=CONCURRENT_SESSIONS_LIMITED, max_concurrent_sessions=4)
    mode, limit = concurrent_policy(cfg)
    assert mode == CONCURRENT_SESSIONS_LIMITED
    assert limit == 4
