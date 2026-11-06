"""Tests réconciliation carrière et journal ETL."""

from __future__ import annotations

from sme_gend.services.carriere_reconciliation import (
    CarriereUnresolvedCounts,
    _scope_sql,
)
from sme_gend.services.etl_erreur_service import log_etl_erreur
from sme_gend.services.mysql_ref_etl import RefEtlCache, _cf


def test_scope_sql_all() -> None:
    sql, params = _scope_sql(None)
    assert sql == "1=1"
    assert params == ()


def test_scope_sql_mois() -> None:
    sql, params = _scope_sql("2026-05")
    assert "annee_mois" in sql
    assert params == ("2026-05",)


def test_unresolved_counts_total() -> None:
    c = CarriereUnresolvedCounts(grade=2, structure=1, fonction=3)
    assert c.total() == 6


def test_sync_ordre_tri_from_pref_ord() -> None:
    from sme_gend.services.carriere_reconciliation import sync_ordre_tri_from_pref_ord

    cache = RefEtlCache()
    cache.grade_by_code[_cf("MDL")] = 5
    cache.grade_by_libelle[_cf("Maréchal des logis")] = 5

    class _Cur:
        def __init__(self) -> None:
            self.updates: list[tuple] = []
            self._fetch_idx = 0

        def execute(self, sql, params=()) -> None:
            if sql.strip().startswith("UPDATE carriere_situation SET ordre_tri"):
                self.updates.append(params)

        def fetchall(self):
            self._fetch_idx += 1
            if self._fetch_idx == 1:
                return [(5, 15)]
            return [(10, "MDL"), (11, "Inconnu")]

    cur = _Cur()
    n = sync_ordre_tri_from_pref_ord(cur, cache, mois=None)
    assert n == 1
    assert cur.updates == [(15, 15, 10)]


def test_log_etl_erreur_executes_insert() -> None:
    class _Cur:
        def __init__(self) -> None:
            self.last: tuple | None = None

        def execute(self, sql, params=()) -> None:
            self.last = (sql, params)

    cur = _Cur()
    log_etl_erreur(
        cur,
        import_lot_id=42,
        table_cible="carriere_situation",
        cle_legacy="12345",
        message="Grade non résolu",
    )
    assert cur.last is not None
    assert "INSERT INTO etl_erreur" in cur.last[0]
    assert cur.last[1][0] == 42
