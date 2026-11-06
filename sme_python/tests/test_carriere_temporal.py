"""Tests segments temporels carrière (Phase D)."""

from __future__ import annotations

from datetime import date

from sme_gend.config.liste_generale_scope import (
    carriere_scope_sql,
    reference_date_for_mois,
)
from sme_gend.services.mysql_import_period import (
    refresh_carriere_segment_dates,
    segment_dates_for_sme_mois,
)


def test_reference_date_for_mois() -> None:
    assert reference_date_for_mois("2026-04") == date(2026, 4, 30)


def test_segment_dates_open() -> None:
    debut, fin = segment_dates_for_sme_mois("2026-04", open_segment=True)
    assert debut == date(2026, 4, 1)
    assert fin is None


def test_segment_dates_closed() -> None:
    debut, fin = segment_dates_for_sme_mois("2026-04", open_segment=False)
    assert debut == date(2026, 4, 1)
    assert fin == date(2026, 4, 30)


def test_carriere_scope_sql_uses_intervals() -> None:
    sql, params = carriere_scope_sql("2026-04")
    assert "date_debut" in sql
    assert "date_fin" in sql
    assert params == ("202604", date(2026,4,30), date(2026,4,30), "202604")
    assert sql.count("%s") == len(params)


def test_carriere_scope_sql_fallback_est_actuelle() -> None:
    sql, params = carriere_scope_sql(None)
    assert "est_actuelle" in sql
    assert params == ()


def test_refresh_carriere_segment_dates() -> None:
    executed: list[str] = []

    class _Cur:
        rowcount = 42

        def execute(self, sql: str, params=None) -> None:
            executed.append(sql)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    class _Conn:
        def cursor(self):
            return _Cur()

    n = refresh_carriere_segment_dates(_Conn())
    assert n == 42
    assert len(executed) == 1
    assert "date_debut" in executed[0]
    assert "date_fin" in executed[0]
