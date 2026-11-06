#!/usr/bin/env python3
"""Recap gains & pertes par mois + ecarts carriere (dry-run, sans ecriture)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.config.sme_context import load_sme_context


def _agent_set(cur, ym: str) -> dict[str, tuple[str, str]]:
    cur.execute(
        """
        SELECT DISTINCT a.legacy_access_id, a.noms, COALESCE(c.grade_libelle, '')
        FROM carriere_situation c
        JOIN sme_periode p ON p.id = c.sme_periode_id
        JOIN agent a ON a.id = c.agent_id
        WHERE p.annee_mois = %s
        """,
        (ym,),
    )
    return {str(r[0]): (str(r[1] or ""), str(r[2] or "")) for r in cur.fetchall()}


def main() -> int:
    s = get_settings()
    mois_actif = (load_sme_context().mois_actif or "").strip()
    print(f"=== Recap gains & pertes (dry-run) - mois actif {mois_actif} ===\n")

    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.annee_mois,
                   SUM(CASE WHEN UPPER(TRIM(COALESCE(m.categorie_mouvement,m.type_mouvement,'')))='GAIN' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN UPPER(TRIM(COALESCE(m.categorie_mouvement,m.type_mouvement,'')))='PERTE' THEN 1 ELSE 0 END),
                   COUNT(*)
            FROM mouvement_effectif m
            JOIN sme_periode p ON p.id = m.sme_periode_id
            WHERE p.annee_mois >= '2026-01' AND p.annee_mois <= '2026-05'
            GROUP BY p.annee_mois
            ORDER BY p.annee_mois
            """
        )
        print("--- mouvement_effectif (officiel) ---")
        rows = cur.fetchall()
        if not rows:
            print("  (aucune ligne 2026-01 a 2026-05)")
        for r in rows:
            print(f"  {r[0]}: {r[1]} gains, {r[2]} pertes ({r[3]} total)")

        cur.execute(
            """
            SELECT p.annee_mois, COUNT(DISTINCT c.agent_id)
            FROM carriere_situation c
            JOIN sme_periode p ON p.id = c.sme_periode_id
            WHERE p.annee_mois >= '2026-01' AND p.annee_mois <= '2026-05'
            GROUP BY p.annee_mois ORDER BY p.annee_mois
            """
        )
        print("\n--- effectif carriere par mois ---")
        eff: dict[str, int] = {}
        for r in cur.fetchall():
            eff[str(r[0])] = int(r[1])
            print(f"  {r[0]}: {r[1]} agents")

        pairs = [("2026-04", "2026-05")]
        if "2026-01" in eff and "2026-04" in eff:
            pairs.insert(0, ("2026-01", "2026-04"))

        print("\n--- pertes deduites (effectif M-1 -> M) ---")
        for prev, nxt in pairs:
            if prev not in eff or nxt not in eff:
                continue
            a, b = _agent_set(cur, prev), _agent_set(cur, nxt)
            pertes = sorted(set(a) - set(b), key=lambda k: a[k][0])
            gains = sorted(set(b) - set(a), key=lambda k: b[k][0])
            print(f"\n  {prev} -> {nxt}: {len(a)} -> {len(b)} (delta {len(b)-len(a):+d})")
            print(f"    Gains deduits: {len(gains)} | Pertes deduites: {len(pertes)}")
            for leg in pertes[:12]:
                n, g = a[leg]
                cur.execute(
                    """
                    SELECT p.annee_mois FROM mouvement_effectif m
                    JOIN agent ag ON ag.id=m.agent_id
                    JOIN sme_periode p ON p.id=m.sme_periode_id
                    WHERE ag.legacy_access_id=%s
                      AND UPPER(TRIM(COALESCE(m.categorie_mouvement,m.type_mouvement,'')))='PERTE'
                    LIMIT 1
                    """,
                    (leg,),
                )
                mv = cur.fetchone()
                tag = f"mouvement={mv[0]}" if mv else "SANS mouvement PERTE"
                print(f"      - [{leg}] {n} ({g}) - {tag}")
            if len(pertes) > 12:
                print(f"      ... +{len(pertes)-12} autres")
    finally:
        conn.close()

    print("\n(Aucune modification supplementaire - rapport dry-run uniquement.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
