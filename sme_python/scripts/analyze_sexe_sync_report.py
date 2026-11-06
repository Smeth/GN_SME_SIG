#!/usr/bin/env python3
"""Analyse post-sync sexe / matricule."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.services.personnel_sexe_reference_sync import (
    load_sexe_reference_bundle,
    normalize_person_name,
    name_token_key,
)

REPORT = _ROOT / "data" / "sexe_sync_reports"


def main() -> int:
    actions = list(
        csv.DictReader((REPORT / "sexe_sync_actions.csv").open(encoding="utf-8-sig"), delimiter=";")
    )
    unresolved = list(
        csv.DictReader((REPORT / "sexe_sync_unresolved.csv").open(encoding="utf-8-sig"), delimiter=";")
    )
    conflicts = [a for a in actions if a.get("status") == "CONFLIT_MAT"]

    s = get_settings()
    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*),
               SUM(sexe IS NULL OR TRIM(sexe) = ''),
               SUM(sexe = 'M'),
               SUM(sexe = 'F')
        FROM agent
        """
    )
    sexe_row = cur.fetchone()
    cur.execute(
        """
        SELECT COUNT(*),
               SUM(matricule_militaire IS NULL OR TRIM(matricule_militaire) = '')
        FROM agent
        """
    )
    mat_row = cur.fetchone()

    refs, dup_mats = load_sexe_reference_bundle(
        _ROOT / "LISTE_GENERALE_SEXE_feminin.xls",
        _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls",
    )

    # Agents sans sexe : proche match dans ref ?
    cur.execute(
        """
        SELECT legacy_access_id, noms, matricule_militaire
        FROM agent
        WHERE sexe IS NULL OR TRIM(sexe) = ''
        ORDER BY noms
        """
    )
    no_sexe_agents = cur.fetchall()
    ref_by_tokens = {r.nom_tokens: r for r in refs}
    ref_by_name = {r.noms: r for r in refs}
    near_miss: list[tuple] = []
    for leg, noms, mat in no_sexe_agents:
        n = normalize_person_name(noms)
        tk = name_token_key(noms)
        if n in ref_by_name or tk in ref_by_tokens:
            near_miss.append((leg, noms, mat, "nom proche ref non resolu"))

    conn.close()

    print("=== Etat base apres sync ===")
    print(f"  Agents total     : {sexe_row[0]}")
    print(f"  Sexe M           : {sexe_row[2]}")
    print(f"  Sexe F           : {sexe_row[3]}")
    print(f"  Sexe manquant    : {sexe_row[1]}")
    print(f"  Matricule manquant: {mat_row[1]} / {mat_row[0]}")

    print("\n=== Blocages / doutes ===")
    print(f"  Non resolus (ref sans agent) : {len(unresolved)}")
    print(f"  Conflits matricule           : {len(conflicts)}")
    print(f"  Doublons mat F+M dans ref    : {len(dup_mats)}")
    if dup_mats:
        print(f"    cles: {dup_mats}")

    uc = Counter(r["sexe"] for r in unresolved)
    print(f"  Non resolus par sexe ref     : F={uc.get('F',0)} M={uc.get('M',0)}")

    by_match = Counter(a.get("match", "") for a in conflicts)
    print(f"  Conflits mat — mode appariement: {dict(by_match)}")

    print("\n=== Exemples conflits matricule (doute nom) ===")
    for a in conflicts[:12]:
        print(
            f"  [{a['legacy_id']}] {a['noms']}\n"
            f"      ref sexe={a['new_sexe']} mat attendu (note) | agent mat={a['old_matricule']}"
        )

    print("\n=== Exemples non resolus ===")
    for r in unresolved[:12]:
        print(f"  ({r['sexe']}) {r['noms']} | mat={r['matricule']} | {r['grade']}")

    if near_miss:
        print(f"\n=== Attention: {len(near_miss)} agents sans sexe mais nom proche dans ref ===")
        for row in near_miss[:10]:
            print(f"  [{row[0]}] {row[1]} | mat={row[2]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
