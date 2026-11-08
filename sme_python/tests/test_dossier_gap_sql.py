"""Règle de détection des fiches différentes du dossier (grade / diplôme).

La détection doit retenir les valeurs du dossier exactement comme la projection
(``sync_carriere_from_dossier``) : sinon le contraste diplôme militaire / civil
fait remonter des écarts qui n'existent pas.
"""

from __future__ import annotations

import sme_gend.config.liste_generale_scope as scope_mod
from sme_gend.services import edition_sme_support as ess


def test_normalize_label_sql_collapses_spaces_and_nbsp() -> None:
    expr = ess.normalize_label_sql("c.diplome_libelle")
    assert expr.startswith("TRIM(")
    assert "'[[:space:]]+'" in expr
    assert "CHAR(0xA0 USING utf8mb4)" in expr  # espace insécable → espace
    assert "c.diplome_libelle" in expr


def test_diplome_rank_prefers_military_before_date() -> None:
    rank = ess.DOSSIER_DIPLOME_RANK_SQL
    assert "categorie = 'MILITAIRE'" in rank
    assert rank.index("MILITAIRE") < rank.index("date_obtention")


def test_passation_rank_uses_latest_dated_passation() -> None:
    rank = ess.DOSSIER_PASSATION_RANK_SQL
    assert "date_passation" in rank
    assert "ORDER BY" in rank


class _Ctx:
    def __init__(self, obj):
        self._obj = obj

    def __enter__(self):
        return self._obj

    def __exit__(self, *exc):
        return False


class _FakeCursor:
    def __init__(self, sqls):
        self._sqls = sqls
        self._rows: list[tuple[str]] = []

    def execute(self, sql, params=None):
        self._sqls.append(sql)
        self._rows = [("7",)]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, sqls):
        self._sqls = sqls

    def cursor(self):
        return _Ctx(_FakeCursor(self._sqls))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeMySQLConnection:
    def __init__(self, sqls):
        self._sqls = sqls

    def connect(self, *, read_only=False):
        return _Ctx(_FakeConn(self._sqls))


def test_gap_query_applies_military_rule_and_normalization(monkeypatch) -> None:
    sqls: list[str] = []
    monkeypatch.setattr(ess, "MySQLConnection", lambda settings: _FakeMySQLConnection(sqls))
    monkeypatch.setattr(
        scope_mod,
        "carriere_scope_for_settings",
        lambda settings, *, alias="c", cursor=None: ("c.est_actuelle = 1", ()),
    )

    ids = ess.fetch_dossier_gap_carriere_ids(object())

    assert ids == {"7"}
    assert len(sqls) == 2
    diploma_sql, grade_sql = sqls
    assert "categorie = 'MILITAIRE'" in diploma_sql
    # les deux côtés de chaque comparaison sont normalisés (jamais un seul)
    assert diploma_sql.count("CHAR(0xA0 USING utf8mb4)") == 2
    assert grade_sql.count("CHAR(0xA0 USING utf8mb4)") == 2
    assert "TRIM(c.diplome_libelle)" not in diploma_sql
