#!/usr/bin/env python3
"""Propositions et application de corrections gains/pertes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.services.mysql_import_period import ensure_sme_periode, last_day_of_month

# Agent -> mois a conserver pour les doublons PERTE (sinon : premier mois).
_KEEP_PERTE_MOIS: dict[str, str] = {
    "24589": "2026-02",  # BILOA : perte reelle fevrier (Excel)
}


def _cdsme(ym: str) -> str:
    y, m = ym.split("-", 1)
    return f"{m.zfill(2)}{y}"


def _agent_set(cur, ym: str) -> set[str]:
    cur.execute(
        """
        SELECT DISTINCT a.legacy_access_id
        FROM carriere_situation c
        JOIN sme_periode p ON p.id = c.sme_periode_id
        JOIN agent a ON a.id = c.agent_id
        WHERE p.annee_mois = %s
        """,
        (ym,),
    )
    return {str(r[0]) for r in cur.fetchall()}


def _periode_id(cur, ym: str) -> int | None:
    cur.execute("SELECT id FROM sme_periode WHERE annee_mois = %s", (ym,))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _fetch_duplicate_pertes(cur) -> list[tuple[str, str, str]]:
    cur.execute(
        """
        SELECT a.legacy_access_id, a.noms,
               GROUP_CONCAT(DISTINCT p.annee_mois ORDER BY p.annee_mois) AS mois
        FROM mouvement_effectif m
        JOIN sme_periode p ON p.id = m.sme_periode_id
        JOIN agent a ON a.id = m.agent_id
        WHERE UPPER(TRIM(COALESCE(m.categorie_mouvement, m.type_mouvement, ''))) = 'PERTE'
          AND p.annee_mois >= '2026-01' AND p.annee_mois <= '2026-05'
        GROUP BY a.legacy_access_id, a.noms
        HAVING COUNT(DISTINCT p.annee_mois) > 1
        ORDER BY a.noms
        """
    )
    return [(str(r[0]), str(r[1] or ""), str(r[2] or "")) for r in cur.fetchall()]


def _fetch_pertes_avril_mai(cur) -> list[str]:
    prev, nxt = "2026-04", "2026-05"
    return sorted(_agent_set(cur, prev) - _agent_set(cur, nxt))


def _delete_perte(cur, legacy_id: str, ym: str) -> int:
    cur.execute(
        """
        DELETE m FROM mouvement_effectif m
        JOIN sme_periode p ON p.id = m.sme_periode_id
        JOIN agent a ON a.id = m.agent_id
        WHERE a.legacy_access_id = %s
          AND p.annee_mois = %s
          AND UPPER(TRIM(COALESCE(m.categorie_mouvement, m.type_mouvement, ''))) = 'PERTE'
        """,
        (legacy_id, ym),
    )
    return int(cur.rowcount or 0)


def _move_perte_to_mois(cur, conn, legacy_id: str, target_ym: str) -> int:
    pid = ensure_sme_periode(conn, target_ym, codsme=_cdsme(target_ym))
    cur.execute(
        """
        SELECT m.id, p.annee_mois
        FROM mouvement_effectif m
        JOIN agent a ON a.id = m.agent_id
        JOIN sme_periode p ON p.id = m.sme_periode_id
        WHERE a.legacy_access_id = %s
          AND UPPER(TRIM(COALESCE(m.categorie_mouvement, m.type_mouvement, ''))) = 'PERTE'
        ORDER BY p.annee_mois
        LIMIT 1
        """,
        (legacy_id,),
    )
    row = cur.fetchone()
    if not row:
        return 0
    mvt_id, src_ym = int(row[0]), str(row[1])
    if src_ym == target_ym:
        return 0
    cur.execute(
        """
        UPDATE mouvement_effectif
        SET sme_periode_id = %s,
            cdsme_derive = %s,
            date_reference = %s
        WHERE id = %s
        """,
        (pid, _cdsme(target_ym), last_day_of_month(target_ym), mvt_id),
    )
    return int(cur.rowcount or 0)


def _print_dry_run(cur) -> None:
    print("=== Propositions correction gains & pertes (dry-run) ===\n")
    dup_rows = _fetch_duplicate_pertes(cur)
    print(f"--- 1) Doublons PERTE ({len(dup_rows)} agents) ---")
    print("   Regle : conserver le mois cible, supprimer les autres.\n")
    for leg, noms, mois in dup_rows:
        parts = [m.strip() for m in mois.split(",")]
        keep = _KEEP_PERTE_MOIS.get(leg, parts[0])
        drop = [m for m in parts if m != keep]
        print(f"   [{leg}] {noms}")
        print(f"      CONSERVER  {keep}  |  SUPPRIMER  {', '.join(drop)}")

    pertes = _fetch_pertes_avril_mai(cur)
    print(f"\n--- 2) Pertes effectif 2026-04 -> 2026-05 ({len(pertes)} agents) ---")
    print("   Regle : CORRIGER_MOIS vers 2026-05.\n")
    for leg in pertes:
        cur.execute(
            """
            SELECT p.annee_mois, m.motif
            FROM mouvement_effectif m
            JOIN agent ag ON ag.id = m.agent_id
            JOIN sme_periode p ON p.id = m.sme_periode_id
            WHERE ag.legacy_access_id = %s
              AND UPPER(TRIM(COALESCE(m.categorie_mouvement, m.type_mouvement, ''))) = 'PERTE'
            ORDER BY p.annee_mois
            """,
            (leg,),
        )
        mvs = cur.fetchall()
        cur.execute("SELECT noms FROM agent WHERE legacy_access_id = %s", (leg,))
        nom = (cur.fetchone() or ("?",))[0]
        if not mvs:
            print(f"   [{leg}] {nom} -> CREER PERTE 2026-05 (non gere ici)")
        elif all(str(r[0]) != "2026-05" for r in mvs):
            src = ", ".join(f"{r[0]}({r[1]})" for r in mvs)
            print(f"   [{leg}] {nom} -> CORRIGER_MOIS 2026-05 (actuel: {src})")
    print("\n(Aucune ecriture — relancer avec --apply pour appliquer.)")


def _apply_corrections(cur, conn) -> tuple[int, int]:
    deleted = 0
    moved = 0

    for leg, noms, mois in _fetch_duplicate_pertes(cur):
        parts = [m.strip() for m in mois.split(",")]
        keep = _KEEP_PERTE_MOIS.get(leg, parts[0])
        for ym in parts:
            if ym == keep:
                continue
            n = _delete_perte(cur, leg, ym)
            if n:
                print(f"  SUPPRIME PERTE [{leg}] {noms} @ {ym}")
                deleted += n

    for leg in _fetch_pertes_avril_mai(cur):
        cur.execute("SELECT noms FROM agent WHERE legacy_access_id = %s", (leg,))
        nom = (cur.fetchone() or ("?",))[0]
        n = _move_perte_to_mois(cur, conn, leg, "2026-05")
        if n:
            print(f"  DEPLACE PERTE [{leg}] {nom} -> 2026-05")
            moved += n

    return deleted, moved


def main() -> int:
    p = argparse.ArgumentParser(description="Correction G/P (dry-run par defaut).")
    p.add_argument("--apply", action="store_true", help="Appliquer les corrections en base")
    args = p.parse_args()

    s = get_settings()
    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
        autocommit=False,
    )
    try:
        cur = conn.cursor()
        if not args.apply:
            _print_dry_run(cur)
            return 0

        print("=== Application corrections gains & pertes ===\n")
        deleted, moved = _apply_corrections(cur, conn)
        conn.commit()
        print(f"\nTermine : {deleted} suppression(s), {moved} deplacement(s).")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
