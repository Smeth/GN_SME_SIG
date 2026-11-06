#!/usr/bin/env python3
"""Exporte les agents sans sexe + colonnes reponse a saisir."""

from __future__ import annotations

import csv
import difflib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pymysql
from sme_gend.config import get_settings
from sme_gend.services.personnel_sexe_reference_sync import (
    load_sexe_reference_bundle,
    name_token_key,
    normalize_matricule_key,
    normalize_person_name,
)

OUT = _ROOT / "data" / "sexe_sync_reports" / "agents_sans_sexe.csv"

# Colonnes a remplir par l'utilisateur (reponse definitive -> MySQL)
USER_FIELDS = ("sexe_correct", "matricule_correct", "note", "statut_applique")


def _valid_mat_key(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s or s in ("/", "-", "0", "?"):
        return ""
    mk = normalize_matricule_key(s)
    return mk if mk and mk != "0" else ""


def _load_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig"), delimiter=";"))
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        leg = (r.get("legacy_id") or "").strip()
        if not leg:
            continue
        out[leg] = {
            "sexe_correct": (r.get("sexe_correct") or r.get("sexe_manuel") or "").strip(),
            "matricule_correct": (r.get("matricule_correct") or "").strip(),
            "note": (r.get("note") or "").strip(),
            "statut_applique": (r.get("statut_applique") or "").strip(),
        }
    return out


def _suggest(ag: dict, refs: list, ref_names: list[str], ref_by_name: dict) -> tuple[str, str, str, str, float]:
    mk = ag["mat_key"]
    if mk:
        hits = [r for r in refs if _valid_mat_key(r.matricule_militaire) == mk]
        if len(hits) == 1:
            r = hits[0]
            return r.sexe, r.matricule_militaire, r.noms, "matricule", 1.0

    hits = [r for r in refs if r.noms == ag["noms"]]
    if len(hits) == 1:
        r = hits[0]
        return r.sexe, r.matricule_militaire, r.noms, "nom_exact", 1.0

    hits = [r for r in refs if r.nom_tokens == ag["tokens"]]
    if len(hits) == 1:
        r = hits[0]
        return r.sexe, r.matricule_militaire, r.noms, "nom_tokens", 0.95

    close = difflib.get_close_matches(ag["noms"], ref_names, n=1, cutoff=0.80)
    if close:
        r = ref_by_name[close[0]]
        score = difflib.SequenceMatcher(None, ag["noms"], r.noms).ratio()
        return r.sexe, r.matricule_militaire, r.noms, "similarite", round(score, 3)

    return "", "", "", "", 0.0


def main() -> int:
    existing = _load_existing(OUT)

    feminin = _ROOT / "LISTE_GENERALE_SEXE_feminin.xls"
    masculin = _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls"
    refs, _ = load_sexe_reference_bundle(feminin, masculin)
    ref_by_name = {r.noms: r for r in refs}
    ref_names = list(ref_by_name.keys())

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
        SELECT a.legacy_access_id, a.noms, a.matricule_militaire,
               COALESCE(c.grade_libelle, '') AS grade
        FROM agent a
        LEFT JOIN carriere_situation c ON c.agent_id = a.id AND c.est_actuelle = 1
        WHERE a.sexe IS NULL OR TRIM(a.sexe) = ''
        ORDER BY a.noms
        """
    )
    rows_db = cur.fetchall()
    conn.close()

    fields = [
        "no",
        "legacy_id",
        "noms",
        "grade",
        "mat_actuel",
        "ref_sexe",
        "ref_matricule",
        "ref_noms",
        "match_score",
        "match_methode",
        "sexe_correct",
        "matricule_correct",
        "note",
        "statut_applique",
    ]

    out_rows: list[dict[str, str]] = []
    prefilled = 0

    for i, (leg, noms, mat, grade) in enumerate(rows_db, start=1):
        leg_s = str(leg or "").strip()
        ag = {
            "noms": normalize_person_name(noms),
            "tokens": name_token_key(noms),
            "mat_key": _valid_mat_key(mat),
        }
        ref_sexe, ref_mat, ref_noms, method, score = _suggest(ag, refs, ref_names, ref_by_name)
        prev = existing.get(leg_s, {})
        sexe_correct = prev.get("sexe_correct", "")
        matricule_correct = prev.get("matricule_correct", "")
        # Pre-remplissage aide (score eleve) si pas encore saisi
        if not sexe_correct and score >= 0.88 and ref_sexe:
            sexe_correct = ref_sexe
            if not matricule_correct and ref_mat:
                matricule_correct = ref_mat
            prefilled += 1
        out_rows.append(
            {
                "no": str(i),
                "legacy_id": leg_s,
                "noms": str(noms or "").strip(),
                "grade": str(grade or "").strip(),
                "mat_actuel": str(mat or "").strip(),
                "ref_sexe": ref_sexe,
                "ref_matricule": ref_mat,
                "ref_noms": ref_noms,
                "match_score": str(score) if score else "",
                "match_methode": method,
                "sexe_correct": sexe_correct,
                "matricule_correct": matricule_correct,
                "note": prev.get("note", ""),
                "statut_applique": prev.get("statut_applique", ""),
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(out_rows)

    print(f"Export : {OUT}")
    print(f"  Agents sans sexe : {len(out_rows)}")
    print("  A REMPLIR : sexe_correct (M ou F) — obligatoire pour mise a jour")
    print("              matricule_correct — optionnel (vide = ne change pas le matricule)")
    print(f"  Pre-remplis auto (score>=0.88) : {prefilled}")
    print("  Appliquer : python scripts/apply_agents_sans_sexe.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
