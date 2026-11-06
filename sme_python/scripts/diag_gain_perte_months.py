#!/usr/bin/env python3
"""Diagnostic Gains & Pertes — pourquoi un mois SME semble vide dans l'écran.

Distingue deux cas bien différents :
  1. Aucune ligne mouvement_effectif n'existe pour ce mois (donnée jamais saisie/importée).
  2. Des lignes existent mais avec un sme_periode_id manquant/orphelin (jointure cassée) —
     l'appli a un repli sur cdsme_derive, donc ce cas ne devrait normalement PAS vider l'écran,
     mais ce script permet de vérifier que ce repli fonctionne bien.
"""
from __future__ import annotations

import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pymysql
from sme_gend.config import get_settings


def main() -> int:
    s = get_settings()
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
            print("=== Total mouvement_effectif ===")
            cur.execute("SELECT COUNT(*) FROM mouvement_effectif")
            print("Lignes totales :", cur.fetchone()[0])

            print("\n=== Répartition par mois (via sme_periode_id -> sme_periode.annee_mois) ===")
            cur.execute(
                """
                SELECT p.annee_mois, COUNT(*)
                FROM mouvement_effectif m
                LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
                GROUP BY p.annee_mois
                ORDER BY p.annee_mois
                """
            )
            rows = cur.fetchall()
            if not rows:
                print("  (table mouvement_effectif vide)")
            for ym, n in rows:
                print(f"  {ym or '(sme_periode_id NULL/introuvable)'}: {n}")

            print("\n=== Répartition par mois dérivé de cdsme_derive (indépendant de sme_periode_id) ===")
            cur.execute(
                """
                SELECT CONCAT(SUBSTRING(cdsme_derive,3,4), '-', LPAD(SUBSTRING(cdsme_derive,1,2),2,'0')) AS ym,
                       COUNT(*)
                FROM mouvement_effectif
                WHERE cdsme_derive REGEXP '^[0-9]{6}$'
                GROUP BY ym
                ORDER BY ym
                """
            )
            rows2 = cur.fetchall()
            if not rows2:
                print("  (aucun cdsme_derive exploitable)")
            for ym, n in rows2:
                print(f"  {ym}: {n}")

            print(
                "\n=== Lignes avec sme_periode_id orphelin/NULL mais cdsme_derive exploitable "
                "(repli actif côté appli) ==="
            )
            cur.execute(
                """
                SELECT COUNT(*) FROM mouvement_effectif m
                LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
                WHERE p.id IS NULL AND m.cdsme_derive REGEXP '^[0-9]{6}$'
                """
            )
            print("Orphelines :", cur.fetchone()[0])

            print("\n=== sme_periode connus (tous les mois du système) vs mouvements liés ===")
            cur.execute("SELECT id, annee_mois, statut FROM sme_periode ORDER BY annee_mois")
            periodes = cur.fetchall()
            if not periodes:
                print("  (table sme_periode vide)")
            for pid, ym, statut in periodes:
                cur.execute(
                    "SELECT COUNT(*) FROM mouvement_effectif WHERE sme_periode_id = %s",
                    (pid,),
                )
                n = cur.fetchone()[0]
                print(f"  {ym} [{statut}] : {n} mouvement(s) lié(s) (sme_periode.id={pid})")

            print(
                "\nConclusion :\n"
                "- Si un mois n'apparaît nulle part ci-dessus (ni par sme_periode_id, ni par "
                "cdsme_derive) : aucune donnée n'a jamais été saisie/importée pour ce mois — "
                "l'écran vide est normal, pas un bug.\n"
                "- Si un mois a des lignes comptées via cdsme_derive mais 0 via sme_periode_id : "
                "la jointure sme_periode_id est cassée pour ces lignes, mais l'appli utilise déjà "
                "un repli sur cdsme_derive donc l'écran devrait quand même les montrer — sinon "
                "c'est un vrai bug à signaler avec ce résultat."
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
