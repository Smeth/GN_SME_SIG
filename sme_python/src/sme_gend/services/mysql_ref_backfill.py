"""Backfill : tab_* → ref_*, FK carrière, dates, mouvement_effectif."""

from __future__ import annotations

from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.mysql_common import ensure_mysql_schema
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.mysql_import_period import ensure_mysql_migrations, first_day_of_month, last_day_of_month
from sme_gend.services.mysql_ref_etl import (
    RefEtlCache,
    _table_exists,
    ensure_ref_etl_cache,
    import_gain_perte_to_mouvement_effectif,
    load_subdiv_flat_rows,
    resolve_carriere_fk_ids,
)


def _row_from_carriere(row: tuple[Any, ...]) -> ListeGeneraleRow:
    """Tuple SELECT carrière + identité agent."""
    return ListeGeneraleRow(
        id=str(row[1] or "").strip(),
        noms=row[2],
        date_naissance=row[3],
        lieu_naissance=row[4],
        grade=row[5],
        date_grade=row[6],
        date_service=row[7],
        ndecision=row[8],
        matricule_militaire=row[9],
        matricule_solde=row[10],
        position_admin=row[11],
        fonction=row[12],
        subdivision_arme=row[13],
        diplome=row[14],
        date_obtention=row[15],
        structure=row[16],
        obs=row[17],
        ordre_tri=row[18],
        presseance=row[19],
    )


def backfill_carriere_fk_and_dates(cur: Any, cache: RefEtlCache) -> dict[str, int]:
    flat = load_subdiv_flat_rows(cur)
    cur.execute(
        """
        SELECT c.id, a.legacy_access_id, a.noms, a.date_naissance, a.lieu_naissance,
               c.grade_libelle, c.date_grade, c.date_service, c.ndecision,
               a.matricule_militaire, a.matricule_solde, c.position_admin_libelle,
               c.fonction_libelle, c.subdivision_arme_libelle, c.diplome_libelle,
               c.date_obtention, c.structure_code, c.obs, c.ordre_tri, c.presseance,
               c.sme_periode_id, p.annee_mois, c.est_actuelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
        """
    )
    rows = cur.fetchall()
    n_fk = 0
    n_dates = 0
    for r in rows:
        cid = int(r[0])
        lg = _row_from_carriere(r)
        fk = resolve_carriere_fk_ids(cur, cache, lg, flat_rows=flat, auto_create=True)
        cur.execute(
            """
            UPDATE carriere_situation SET
              structure_id=%s, grade_id=%s, position_admin_id=%s,
              fonction_id=%s, subdivision_arme_id=%s
            WHERE id=%s
            """,
            (
                fk["structure_id"],
                fk["grade_id"],
                fk["position_admin_id"],
                fk["fonction_id"],
                fk["subdivision_arme_id"],
                cid,
            ),
        )
        n_fk += 1
        ym = str(r[21] or "").strip() if r[21] else None
        if ym:
            debut = first_day_of_month(ym)
            fin = last_day_of_month(ym)
            date_fin = fin if int(r[22] or 0) == 0 else None
            cur.execute(
                """
                UPDATE carriere_situation SET date_debut=%s, date_fin=%s WHERE id=%s
                """,
                (debut, date_fin, cid),
            )
            n_dates += 1
    return {"carriere_fk": n_fk, "carriere_dates": n_dates}


def run_mysql_ref_backfill(settings: Settings, *, rebuild_mouvements: bool = True) -> dict[str, Any]:
    """
    1. Charge ref_* (sync tab_* → ref_* seulement si tables legacy présentes)
    2. Remplit les FK et dates sur toutes les carrières
    3. Optionnel : reconstruit mouvement_effectif depuis tab_gain_perte (si table présente)
    """
    ensure_mysql_schema(settings)
    ensure_mysql_migrations(settings)
    stats: dict[str, Any] = {}
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cache = ensure_ref_etl_cache(cur)
            stats["etl"] = dict(cache.counts)
            stats["carriere"] = backfill_carriere_fk_and_dates(cur, cache)
            from sme_gend.services.carriere_reconciliation import sync_ordre_tri_from_pref_ord
            from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db

            cache2 = load_ref_cache_from_db(cur)
            stats["ordre_tri"] = sync_ordre_tri_from_pref_ord(cur, cache2, mois=None)
            if rebuild_mouvements and _table_exists(cur, "tab_gain_perte"):
                cur.execute("SELECT id FROM sme_periode ORDER BY annee_mois DESC LIMIT 1")
                row = cur.fetchone()
                pid = int(row[0]) if row else 0
                cur.execute("SELECT annee_mois FROM sme_periode WHERE id=%s", (pid,))
                ym_row = cur.fetchone()
                ym = str(ym_row[0]) if ym_row else "2000-01"
                stats["mouvement_effectif"] = import_gain_perte_to_mouvement_effectif(
                    cur,
                    cache,
                    sme_periode_id=pid,
                    import_lot_id=None,
                    mois_import=ym,
                    replace_period_only=False,
                )
    return stats
