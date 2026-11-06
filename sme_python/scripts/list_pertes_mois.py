#!/usr/bin/env python3
"""Liste les pertes mouvement_effectif pour le mois actif."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.config.sme_context import load_sme_context


def main() -> int:
    s = get_settings()
    mois = (load_sme_context().mois_actif or "2026-05").strip()
    print(f"Mois actif : {mois}\n")

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
            cur.execute(
                """
                SELECT a.legacy_access_id, a.noms,
                       COALESCE(m.grade_snapshot, '') AS grade,
                       COALESCE(m.motif, '') AS motif,
                       COALESCE(m.observ, '') AS observ,
                       p.annee_mois,
                       (SELECT COUNT(*) FROM carriere_situation c
                        WHERE c.agent_id = a.id AND c.est_actuelle = 1) AS en_effectif
                FROM mouvement_effectif m
                INNER JOIN agent a ON a.id = m.agent_id
                INNER JOIN sme_periode p ON p.id = m.sme_periode_id AND p.annee_mois = %s
                WHERE UPPER(TRIM(COALESCE(m.categorie_mouvement, m.type_mouvement, ''))) = 'PERTE'
                ORDER BY a.noms
                """,
                (mois,),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    print(f"Nombre de pertes enregistrées : {len(rows)}\n")
    if not rows:
        print("(Aucune perte pour ce mois en mouvement_effectif.)")
        return 0

    for i, (leg, noms, grade, motif, observ, _ym, eff) in enumerate(rows, 1):
        label = observ or motif or "—"
        if observ and motif and observ.upper() != motif.upper():
            label = f"{motif} / {observ}"
        print(
            f"{i}. [{leg}] {noms}\n"
            f"   Grade : {grade or '—'}\n"
            f"   Motif : {label}\n"
            f"   Dans effectif mai : {'Oui' if eff else 'Non (hors effectif)'}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
