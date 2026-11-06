#!/usr/bin/env python3
"""Export rapprochement : conflits matricule + noms proches non resolus."""

from __future__ import annotations

import csv
import difflib
import sys
from dataclasses import dataclass
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

REPORT = _ROOT / "data" / "sexe_sync_reports"
OUT = REPORT / "sexe_sync_rapprochement.csv"


@dataclass(slots=True)
class Candidate:
    legacy_id: str
    noms: str
    sexe: str | None
    matricule: str
    score: float
    source: str


def _load_agents(cur) -> list[dict]:
    cur.execute(
        """
        SELECT legacy_access_id, noms, sexe, matricule_militaire
        FROM agent
        ORDER BY noms
        """
    )
    out: list[dict] = []
    for leg, noms, sexe, mat in cur.fetchall():
        n = normalize_person_name(noms)
        out.append(
            {
                "legacy_id": str(leg or "").strip(),
                "noms": n,
                "noms_raw": str(noms or "").strip(),
                "nom_tokens": name_token_key(noms),
                "sexe": sexe,
                "matricule": (mat or "").strip(),
                "mat_key": normalize_matricule_key(mat),
            }
        )
    return out


def _best_name_candidates(ref_noms: str, agents: list[dict], *, limit: int = 3) -> list[Candidate]:
    ref_tok = name_token_key(ref_noms)
    scored: list[Candidate] = []
    for ag in agents:
        ratio = difflib.SequenceMatcher(None, ref_noms, ag["noms"]).ratio()
        tok_ratio = difflib.SequenceMatcher(None, ref_tok, ag["nom_tokens"]).ratio()
        score = max(ratio, tok_ratio)
        if score >= 0.82:
            scored.append(
                Candidate(
                    legacy_id=ag["legacy_id"],
                    noms=ag["noms_raw"],
                    sexe=ag["sexe"],
                    matricule=ag["matricule"],
                    score=round(score, 3),
                    source="similarite_nom",
                )
            )
    scored.sort(key=lambda c: (-c.score, c.noms))
    return scored[:limit]


def _mat_candidate(ref_mat_key: str, agents: list[dict]) -> Candidate | None:
    for ag in agents:
        if ag["mat_key"] and ag["mat_key"] == ref_mat_key:
            return Candidate(
                legacy_id=ag["legacy_id"],
                noms=ag["noms_raw"],
                sexe=ag["sexe"],
                matricule=ag["matricule"],
                score=1.0,
                source="matricule_normalise",
            )
    return None


def main() -> int:
    feminin = _ROOT / "LISTE_GENERALE_SEXE_feminin.xls"
    masculin = _ROOT / "LISTE_GENERALE_SEXE_Masculin.xls"
    refs, dup_mats = load_sexe_reference_bundle(feminin, masculin)

    actions = list(
        csv.DictReader((REPORT / "sexe_sync_actions.csv").open(encoding="utf-8-sig"), delimiter=";")
    )
    unresolved = list(
        csv.DictReader((REPORT / "sexe_sync_unresolved.csv").open(encoding="utf-8-sig"), delimiter=";")
    )
    conflicts = {a["legacy_id"]: a for a in actions if a.get("status") == "CONFLIT_MAT"}

    s = get_settings()
    conn = pymysql.connect(
        host=s.mysql_host,
        port=s.mysql_port,
        user=s.mysql_user,
        password=s.mysql_password,
        database=s.mysql_database,
        charset="utf8mb4",
    )
    agents = _load_agents(conn.cursor())
    conn.close()

    agent_by_name = {ag["noms"]: ag for ag in agents}
    agent_names = list(agent_by_name.keys())
    ref_by_name = {r.noms: r for r in refs}
    ref_names = list(ref_by_name.keys())
    agent_by_mat = {ag["mat_key"]: ag for ag in agents if ag["mat_key"]}

    rows_out: list[dict[str, str]] = []

    # 1) Conflits matricule deja apparies
    for leg, act in sorted(conflicts.items(), key=lambda x: x[1]["noms"]):
        note = act.get("note", "")
        ref_mat = ""
        if "ref=" in note:
            ref_mat = note.split("ref=")[-1].strip().strip("'")
        rows_out.append(
            {
                "categorie": "CONFLIT_MAT",
                "action_proposee": "Verifier matricule — sexe deja MAJ en base",
                "legacy_id_base": leg,
                "noms_base": act["noms"],
                "sexe_base": act.get("new_sexe") or act.get("old_sexe") or "",
                "mat_base": act.get("old_matricule") or "",
                "ref_sexe": act.get("new_sexe") or "",
                "ref_matricule": ref_mat,
                "ref_noms": act["noms"],
                "suggestion_legacy_id": "",
                "suggestion_noms": "",
                "suggestion_score": "",
                "commentaire": note,
            }
        )

    # 2) Non resolus avec suggestions
    for ur in unresolved:
        ref_noms = normalize_person_name(ur.get("noms"))
        ref_mat = (ur.get("matricule") or "").strip()
        ref_mat_key = normalize_matricule_key(ref_mat)
        mat_c = agent_by_mat.get(ref_mat_key) if ref_mat_key else None
        if mat_c:
            sug = Candidate(
                legacy_id=mat_c["legacy_id"],
                noms=mat_c["noms_raw"],
                sexe=mat_c["sexe"],
                matricule=mat_c["matricule"],
                score=1.0,
                source="matricule_normalise",
            )
        else:
            close = difflib.get_close_matches(ref_noms, agent_names, n=1, cutoff=0.84)
            if close:
                ag = agent_by_name[close[0]]
                sug = Candidate(
                    legacy_id=ag["legacy_id"],
                    noms=ag["noms_raw"],
                    sexe=ag["sexe"],
                    matricule=ag["matricule"],
                    score=round(difflib.SequenceMatcher(None, ref_noms, ag["noms"]).ratio(), 3),
                    source="similarite_nom",
                )
            else:
                sug = None
        dup = "OUI" if ref_mat_key in dup_mats else ""
        rows_out.append(
            {
                "categorie": "NON_RESOLU",
                "action_proposee": "Valider suggestion ou creer lien manuel",
                "legacy_id_base": sug.legacy_id if sug else "",
                "noms_base": sug.noms if sug else "",
                "sexe_base": (sug.sexe or "") if sug else "",
                "mat_base": sug.matricule if sug else "",
                "ref_sexe": ur.get("sexe") or "",
                "ref_matricule": ref_mat,
                "ref_noms": ref_noms,
                "suggestion_legacy_id": sug.legacy_id if sug else "",
                "suggestion_noms": sug.noms if sug else "",
                "suggestion_score": str(sug.score) if sug else "",
                "commentaire": f"doublon_mat_ref={dup}" if dup else "",
            }
        )

    # 3) Agents sans sexe mais nom proche ref
    for ag in agents:
        if ag["sexe"] or ag["legacy_id"] in conflicts:
            continue
        ref = ref_by_name.get(ag["noms"])
        score = 1.0
        if not ref:
            close = difflib.get_close_matches(ag["noms"], ref_names, n=1, cutoff=0.88)
            if not close:
                continue
            ref = ref_by_name[close[0]]
            score = difflib.SequenceMatcher(None, ag["noms"], ref.noms).ratio()
        rows_out.append(
            {
                "categorie": "NOM_PROCHE_SANS_SEXE",
                "action_proposee": "Confirmer identite puis MAJ sexe+mat",
                "legacy_id_base": ag["legacy_id"],
                "noms_base": ag["noms_raw"],
                "sexe_base": "",
                "mat_base": ag["matricule"],
                "ref_sexe": ref.sexe,
                "ref_matricule": ref.matricule_militaire,
                "ref_noms": ref.noms,
                "suggestion_legacy_id": ag["legacy_id"],
                "suggestion_noms": ag["noms_raw"],
                "suggestion_score": str(round(score, 3)),
                "commentaire": f"source={ref.source_file}",
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "categorie",
        "action_proposee",
        "legacy_id_base",
        "noms_base",
        "sexe_base",
        "mat_base",
        "ref_sexe",
        "ref_matricule",
        "ref_noms",
        "suggestion_legacy_id",
        "suggestion_noms",
        "suggestion_score",
        "commentaire",
    ]
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
        w.writeheader()
        w.writerows(rows_out)

    from collections import Counter

    c = Counter(r["categorie"] for r in rows_out)
    print(f"Export : {OUT}")
    for k, v in sorted(c.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
