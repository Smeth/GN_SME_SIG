from dataclasses import replace
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.config.liste_generale_scope import carriere_scope_sql
from sme_gend.services.membership_scope import membership_projection_enabled


def test_membership_scope_is_opt_in_and_correlates_unaliased_outer_table():
    legacy, params = carriere_scope_sql('2026-11', alias='')
    scoped, scoped_params = carriere_scope_sql('2026-11', alias='', membership=True)
    assert 'sme_month_membership' not in legacy
    assert 'workforce_member.agent_id=carriere_situation.agent_id' in scoped
    assert 'workforce_member.sme_periode_id=carriere_situation.sme_periode_id' in scoped
    assert scoped_params == params and scoped.count('%s') == len(params)


def test_membership_capability_requires_tables_and_applied_ledger():
    settings = Settings(database_path=Path('.'))
    class Cursor:
        def __init__(self, values): self.values = iter(values); self.sql = []
        def execute(self, sql, params=()): self.sql.append(sql)
        def fetchone(self): return next(self.values)
    for values, expected in [([(0,)], False), ([(3,), ('failed',)], False), ([(3,), ('applied',)], True)]:
        cursor = Cursor(values)
        assert membership_projection_enabled(settings, cursor=cursor) is expected
        assert not any('CREATE' in sql or 'ALTER' in sql for sql in cursor.sql)


def test_capability_cache_is_database_specific_within_transaction(monkeypatch):
    cache = {}
    monkeypatch.setattr('sme_gend.services.membership_scope.transaction_local_cache', lambda namespace: cache)
    first = Settings(database_path=Path('.'), mysql_database='first')
    second = replace(first, mysql_database='second')
    class Cursor:
        def __init__(self): self.count = 0
        def execute(self, sql, params=()): self.count += 1
        def fetchone(self): return (0,)
    cursor = Cursor()
    assert not membership_projection_enabled(first, cursor=cursor)
    assert not membership_projection_enabled(first, cursor=cursor)
    assert not membership_projection_enabled(second, cursor=cursor)
    assert cursor.count == 2
