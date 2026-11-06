"""Façade NumeriqueSatelliteRepository — délégation upsert/delete malade."""

from __future__ import annotations

from unittest.mock import MagicMock

from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository


def test_facade_delegates_malade_writes(monkeypatch) -> None:
    impl = MagicMock()
    monkeypatch.setattr(
        "sme_gend.repositories.mysql_numerique_repository.MySQLNumeriqueRepository",
        lambda settings: impl,
    )
    repo = NumeriqueSatelliteRepository(MagicMock())
    detail = MaladeDetail(date_constat="2026-01-01", pathologie="grippe", obs="")
    repo.upsert_malade("42", detail)
    repo.delete_malade("42")
    impl.upsert_malade.assert_called_once_with("42", detail)
    impl.delete_malade.assert_called_once_with("42")
