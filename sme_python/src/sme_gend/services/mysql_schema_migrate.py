"""Migration données : liste_generale → agent + carriere_situation."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection


def snapshot_liste_generale(settings: Settings) -> list[tuple]:
    """Lit ``liste_generale`` avant application du schéma unifié (si la table existe)."""
    mc = MySQLConnection(settings)
    with mc.connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = DATABASE() AND table_name = 'liste_generale'
                """
            )
            if not cur.fetchone()[0]:
                return []
            cur.execute(
                """
                SELECT id, id_acces, noms, date_n, lieu_n, grade, date_grade, date_service,
                       ndecision, matmilitaire, matsolde, padmin, fonction, sub_arme,
                       diplome, date_obtention, structure, obs, ordre_tri, presseance
                FROM liste_generale
                """
            )
            return list(cur.fetchall())


def import_liste_snapshot_to_agent(
    settings: Settings, rows: list[tuple]
) -> tuple[int, int]:
    """Insère un snapshot ``liste_generale`` dans agent / carriere_situation."""
    if not rows:
        return 0, 0
    agents = 0
    carrieres = 0
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for r in rows:
                leg = (str(r[1] or r[0] or "").strip()) or None
                if not leg:
                    continue
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                ex = cur.fetchone()
                if ex:
                    aid = int(ex[0])
                else:
                    cur.execute(
                        """
                        INSERT INTO agent (legacy_access_id, noms, date_naissance, lieu_naissance,
                          matricule_militaire, matricule_solde)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        """,
                        (leg, r[2], r[3], r[4], r[9], r[10]),
                    )
                    aid = cur.lastrowid
                    agents += 1
                cur.execute(
                    "SELECT id FROM carriere_situation WHERE agent_id=%s AND est_actuelle=1",
                    (aid,),
                )
                if cur.fetchone():
                    cur.execute(
                        """
                        UPDATE carriere_situation SET
                          grade_libelle=%s, date_grade=%s, date_service=%s, ndecision=%s,
                          position_admin_libelle=%s, fonction_libelle=%s,
                          subdivision_arme_libelle=%s, diplome_libelle=%s, date_obtention=%s,
                          structure_code=%s, obs=%s, ordre_tri=%s, presseance=%s
                        WHERE agent_id=%s AND est_actuelle=1
                        """,
                        (
                            r[5], r[6], r[7], r[8], r[11], r[12], r[13], r[14], r[15],
                            r[16], r[17], r[18], r[19], aid,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO carriere_situation (
                          agent_id, est_actuelle, grade_libelle, date_grade, date_service,
                          ndecision, position_admin_libelle, fonction_libelle,
                          subdivision_arme_libelle, diplome_libelle, date_obtention,
                          structure_code, obs, ordre_tri, presseance, date_debut
                        ) VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,CURDATE())
                        """,
                        (
                            aid, r[5], r[6], r[7], r[8], r[11], r[12], r[13], r[14], r[15],
                            r[16], r[17], r[18], r[19],
                        ),
                    )
                    carrieres += 1
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository

    MySQLAgentRepository(settings).link_satellite_agent_ids()
    return agents, carrieres


def migrate_liste_generale_to_agent(settings: Settings) -> tuple[int, int]:
    """Raccourci : snapshot + import (table ``liste_generale`` encore présente)."""
    rows = snapshot_liste_generale(settings)
    return import_liste_snapshot_to_agent(settings, rows)
