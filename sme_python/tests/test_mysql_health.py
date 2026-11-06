"""Tests santé MySQL (messages déploiement)."""

from __future__ import annotations

from sme_gend.services.mysql_health import format_mysql_error_for_user


def test_format_mysql_error_connection_refused() -> None:
    msg = format_mysql_error_for_user("(2003, \"Can't connect to MySQL server on '192.168.1.10'\")")
    assert "injoignable" in msg.casefold() or "192.168" in msg


def test_format_mysql_error_access_denied() -> None:
    msg = format_mysql_error_for_user("(1045, \"Access denied for user 'x'@'host'\")")
    assert "refusé" in msg.casefold() or "mot de passe" in msg.casefold()


def test_format_mysql_error_unknown_database() -> None:
    msg = format_mysql_error_for_user("(1049, \"Unknown database 'smegend'\")")
    assert "base" in msg.casefold()


def test_format_mysql_error_deadlock_recommends_retry() -> None:
    msg = format_mysql_error_for_user(
        "(1213, 'Deadlock found when trying to get lock; try restarting transaction')"
    )
    assert "conflit temporaire" in msg.casefold()
    assert "réessayez" in msg.casefold()
