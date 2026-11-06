#!/usr/bin/env python3
"""Migre tab_mission / tab_malade / tab_deserteur → agent_* (si tables v2 existent)."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sme_gend.config import get_settings
from sme_gend.services.access_date_parse import parse_access_date


def main() -> int:
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.mysql_import_period import apply_numerique_agent_schema

    settings = get_settings()
    apply_numerique_agent_schema(settings)
    mc = MySQLConnection(settings)
    stats = {"mission": 0, "malade": 0, "desertion": 0}
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_schema=DATABASE() AND table_name='tab_mission'")
            if not cur.fetchone()[0]:
                print("Pas de tab_mission — rien à migrer.")
                return 0

            cur.execute("DELETE FROM agent_mission")
            cur.execute(
                """
                SELECT t.id, t.agent_id, t.nature, t.lieu_mission, t.refnds, t.date_depart
                FROM tab_mission t
                """
            )
            for leg, aid, nat, lieu, ref, dd in cur.fetchall():
                if not aid:
                    cur.execute("SELECT id FROM agent WHERE legacy_access_id=%s", (str(leg),))
                    r = cur.fetchone()
                    if not r:
                        continue
                    aid = int(r[0])
                cur.execute(
                    """
                    INSERT INTO agent_mission (
                      agent_id, date_debut, nature_libelle, lieu, reference_dossier,
                      source_ligne_legacy
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (aid, parse_access_date(dd), nat, lieu, ref, str(leg)),
                )
                stats["mission"] += 1

            cur.execute("DELETE FROM agent_malade")
            cur.execute("SELECT id, agent_id, date_constat, pathologie, obs FROM tab_malade")
            for leg, aid, dc, path, obs in cur.fetchall():
                d0 = parse_access_date(dc)
                if not d0:
                    continue
                if not aid:
                    cur.execute("SELECT id FROM agent WHERE legacy_access_id=%s", (str(leg),))
                    r = cur.fetchone()
                    if not r:
                        continue
                    aid = int(r[0])
                cur.execute(
                    """
                    INSERT INTO agent_malade (
                      agent_id, date_debut, pathologie, observation, source_ligne_legacy
                    ) VALUES (%s,%s,%s,%s,%s)
                    """,
                    (aid, d0, path, obs, str(leg)),
                )
                stats["malade"] += 1

            cur.execute("DELETE FROM agent_desertion")
            cur.execute("SELECT id, agent_id, dat_desertion FROM tab_deserteur")
            for leg, aid, dd in cur.fetchall():
                ddes = parse_access_date(dd)
                if not ddes:
                    continue
                if not aid:
                    cur.execute("SELECT id FROM agent WHERE legacy_access_id=%s", (str(leg),))
                    r = cur.fetchone()
                    if not r:
                        continue
                    aid = int(r[0])
                cur.execute(
                    """
                    INSERT INTO agent_desertion (agent_id, date_desertion, source_ligne_legacy)
                    VALUES (%s,%s,%s)
                    ON DUPLICATE KEY UPDATE date_desertion=VALUES(date_desertion)
                    """,
                    (aid, ddes, str(leg)),
                )
                stats["desertion"] += 1

            cur.execute(
                """
                UPDATE mouvement_effectif m
                SET categorie_mouvement = CASE
                  WHEN UPPER(TRIM(type_mouvement)) = 'GAIN' THEN 'GAIN'
                  WHEN UPPER(TRIM(type_mouvement)) = 'PERTE' THEN 'PERTE'
                  ELSE UPPER(TRIM(type_mouvement))
                END,
                motif = LEFT(UPPER(TRIM(observ)), 64)
                WHERE categorie_mouvement IS NULL
                """
            )
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
