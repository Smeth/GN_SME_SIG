"""Régression PyMySQL : %% dans LIKE quand la requête contient aussi des %s."""


def _padmin_where_clause_present() -> str:
    from sme_gend.repositories.mysql_agent_repository import _padmin_where_clause

    sql, _ = _padmin_where_clause("present")
    return sql


def test_padmin_where_like_does_not_consume_query_params() -> None:
    join = (
        "((c.date_debut IS NOT NULL AND c.date_debut <= %s "
        "AND (c.date_fin IS NULL OR c.date_fin >= %s)) OR "
        "(c.date_debut IS NULL AND c.sme_periode_id = "
        "(SELECT id FROM sme_periode WHERE annee_mois = %s LIMIT 1)))"
    )
    where_sql = _padmin_where_clause_present()
    sql = (
        f"SELECT 1 FROM agent a INNER JOIN carriere_situation c "
        f"ON c.agent_id = a.id AND {join} WHERE {where_sql}"
    )
    params = ("2026-04-30", "2026-04-30", "2026-04")
    rendered = sql % params
    assert rendered.count("2026-04-30") == 2
    assert "2026-04" in rendered
    assert "%s" not in rendered
