#!/usr/bin/env python3
"""Diagnostic effectif mai — compare comptages."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pymysql
from sme_gend.config import get_settings
from sme_gend.config.liste_generale_scope import carriere_scope_sql, resolve_liste_view_mois
from sme_gend.config.sme_context import load_sme_context


def main() -> int:
    s = get_settings()
    ctx = load_sme_context()
    view_mois = resolve_liste_view_mois(s)
    print("mois_actif:", ctx.mois_actif)
    print("view_mois (liste):", view_mois)

    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository

    repo = MySQLAgentRepository(s)
    print("fetch_all (UI liste):", len(repo.fetch_all()))

    scope, params = carriere_scope_sql(view_mois, alias="c")
    scope2 = scope.replace("c.", "c2.")

    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM carriere_situation WHERE est_actuelle = 1")
            print("est_actuelle=1 (SoT import):", cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT COUNT(DISTINCT a.id)
                FROM agent a
                INNER JOIN carriere_situation c ON c.agent_id = a.id AND ({scope})
                """,
                params,
            )
            print("scope date (SQL liste):", cur.fetchone()[0])

            cur.execute(
                f"""
                SELECT c.id, a.legacy_access_id, a.noms, c.est_actuelle, p.annee_mois,
                       c.date_debut, c.date_fin
                FROM agent a
                INNER JOIN carriere_situation c ON c.agent_id = a.id AND ({scope})
                LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                WHERE c.est_actuelle = 0
                ORDER BY a.noms
                """,
                params,
            )
            extra = cur.fetchall()
            print(f"\n+6 fantômes : dans liste UI mais est_actuelle=0 ({len(extra)}):")
            for r in extra:
                print(" ", r)

            cur.execute(
                f"""
                SELECT c.id, a.legacy_access_id, a.noms, p.annee_mois, c.date_debut, c.date_fin
                FROM carriere_situation c
                INNER JOIN agent a ON a.id = c.agent_id
                LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                WHERE c.est_actuelle = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM carriere_situation c2
                    WHERE c2.agent_id = c.agent_id AND ({scope2})
                  )
                ORDER BY a.noms
                """,
                params,
            )
            missing = cur.fetchall()
            print(f"\nest_actuelle=1 mais absents du scope liste ({len(missing)}):")
            for r in missing:
                print(" ", r)

            # pertes avril encore couvertes par intervalle mai
            cur.execute(
                f"""
                SELECT a.legacy_access_id, a.noms, p.annee_mois, c.date_debut, c.date_fin,
                       m.observ
                FROM agent a
                INNER JOIN carriere_situation c ON c.agent_id = a.id AND ({scope})
                LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                LEFT JOIN mouvement_effectif m ON m.agent_id = a.id
                LEFT JOIN sme_periode pm ON pm.id = m.sme_periode_id AND pm.annee_mois = '2026-05'
                WHERE c.est_actuelle = 0
                  AND UPPER(COALESCE(m.categorie_mouvement, m.type_mouvement, '')) = 'PERTE'
                ORDER BY a.noms
                """,
                params,
            )
            pertes_scope = cur.fetchall()
            print(f"\nPERTE enregistrée + segment scope mai sans est_actuelle ({len(pertes_scope)}):")
            for r in pertes_scope:
                print(" ", r)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
