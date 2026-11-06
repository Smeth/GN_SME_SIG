#!/usr/bin/env python3
"""Audit rapide cohérence SoT — sortie JSON/texte."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pymysql

from sme_gend.config import get_settings
from sme_gend.services.etl_erreur_service import count_etl_erreurs
from sme_gend.services.sot_maintenance import collect_sot_metrics
from sme_gend.services.sot_status_service import fetch_sot_status_summary


def main() -> int:
    s = get_settings()
    summary = fetch_sot_status_summary(s)
    out: dict = {
        "mois": summary.mois,
        "gaps_dossier_liste": len(summary.gap_ids),
        "fk_non_resolues": len(summary.fk_unresolved_ids),
        "etl_erreur": count_etl_erreurs(s),
    }
    try:
        metrics = collect_sot_metrics(s, mois="2026-05")
        out["ecarts_est_actuelle"] = {
            "grade": metrics.ecarts_grade,
            "diplome": metrics.ecarts_diplome,
            "affectation": metrics.ecarts_affectation,
        }
        out["agents_sans_est_actuelle"] = metrics.agents_sans_actuelle
        out["segments_sans_date_debut"] = metrics.segments_sans_debut
    except Exception as exc:
        out["metrics_detail"] = f"partiel: {exc}"
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
                SELECT COUNT(*) FROM (
                  SELECT agent_id FROM carriere_situation
                  WHERE est_actuelle = 1 GROUP BY agent_id HAVING COUNT(*) > 1
                ) t
                """
            )
            out["agents_multi_est_actuelle"] = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND table_name = 'ref_fonction_structure'
                """
            )
            out["table_ref_fonction_structure"] = bool(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM agent_passation_grade pg
                WHERE EXISTS (
                  SELECT 1 FROM agent_passation_grade pg2
                  WHERE pg2.agent_id = pg.agent_id AND pg2.id <> pg.id
                    AND COALESCE(pg2.date_passation, '1900-01-01')
                      = COALESCE(pg.date_passation, '1900-01-01')
                )
                """
            )
            out["passations_doublon_date"] = int(cur.fetchone()[0])
            cur.execute(
                """
                SELECT COUNT(*) FROM agent_diplome d
                WHERE EXISTS (
                  SELECT 1 FROM agent_diplome d2
                  WHERE d2.agent_id = d.agent_id AND d2.id <> d.id
                    AND COALESCE(d2.date_obtention, '1900-01-01')
                      = COALESCE(d.date_obtention, '1900-01-01')
                )
                """
            )
            out["diplomes_doublon_date"] = int(cur.fetchone()[0])
            try:
                from sme_gend.services.career_import_alerts import scan_all_career_alerts

                out["alertes_parcours"] = len(
                    scan_all_career_alerts(s, mois_focus="2026-05", limit=5000)
                )
            except Exception as exc:
                out["alertes_parcours"] = f"scan indisponible: {exc}"
    finally:
        conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
