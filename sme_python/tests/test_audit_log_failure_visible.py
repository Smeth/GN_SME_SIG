from __future__ import annotations

import logging

from sme_gend.repositories import app_config_repository
from sme_gend.services import audit_log


def test_mysql_audit_failure_is_logged(monkeypatch, caplog) -> None:
    class _Repository:
        def __init__(self, _settings) -> None:
            pass

        def table_exists(self, _table: str) -> bool:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(app_config_repository, "AppConfigRepository", _Repository)
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: object())

    import pytest
    with caplog.at_level(logging.ERROR, logger="sme_gend.services.audit_log"), pytest.raises(RuntimeError):
        audit_log._append_audit_mysql(action="update", module="personnel")

    assert "Échec d'écriture dans audit_journal" in caplog.text
    assert "database unavailable" not in caplog.text


def test_personnel_audit_batch_uses_one_repository_call(monkeypatch) -> None:
    received: list[list[dict]] = []

    class _Repository:
        def __init__(self, _settings) -> None:
            pass

        def append_audit_batch(self, entries: list[dict]) -> None:
            received.append(entries)

    monkeypatch.setattr(app_config_repository, "AppConfigRepository", _Repository)
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: object())
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_audit_actor", lambda: "tester")
    monkeypatch.setattr(audit_log, "_json_mirror_enabled", lambda: False)

    audit_log.append_personnel_audit_batch(
        [
            {"personnel_id": "A1", "changes": {"grade": {"before": "A", "after": "B"}}},
            {"personnel_id": "A2", "changes": {"grade": {"before": "B", "after": "C"}}},
        ]
    )

    assert len(received) == 1
    assert [entry["personnel_id"] for entry in received[0]] == ["A1", "A2"]


def test_personnel_audit_batch_fallback_single_append(monkeypatch) -> None:
    singles: list[str] = []

    class _Repository:
        def __init__(self, _settings) -> None:
            pass

        def append_audit(self, **kwargs) -> None:
            singles.append(str(kwargs.get("personnel_id") or ""))

    monkeypatch.setattr(app_config_repository, "AppConfigRepository", _Repository)
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: object())
    monkeypatch.setattr("sme_gend.services.auth_context.resolve_audit_actor", lambda: "tester")
    monkeypatch.setattr(audit_log, "_json_mirror_enabled", lambda: False)

    audit_log.append_personnel_audit_batch(
        [
            {"personnel_id": "X1", "changes": {}},
            {"personnel_id": "X2", "changes": {}},
        ]
    )

    assert singles == ["X1", "X2"]
