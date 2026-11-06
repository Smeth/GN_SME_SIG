"""Détection pilote ODBC Access."""

from __future__ import annotations

from sme_gend.services.access_odbc import pick_access_driver


def test_pick_access_driver_prefers_exact_match(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.access_odbc.installed_odbc_drivers",
        lambda: ["SQL Server", "Microsoft Access Driver (*.mdb, *.accdb)"],
    )
    assert (
        pick_access_driver("Microsoft Access Driver (*.mdb, *.accdb)")
        == "Microsoft Access Driver (*.mdb, *.accdb)"
    )


def test_pick_access_driver_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.services.access_odbc.installed_odbc_drivers",
        lambda: ["Microsoft Access Driver (*.mdb)"],
    )
    assert pick_access_driver("Inconnu") == "Microsoft Access Driver (*.mdb)"
