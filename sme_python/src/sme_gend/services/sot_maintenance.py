"""Maintenance SoT : métriques SQL et resynchronisation batch."""

from __future__ import annotations

from sme_gend.services.automatic_maintenance import automatic_write

from dataclasses import dataclass, field
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.carriere_reconciliation import (
    CarriereUnresolvedCounts,
    reconcile_carriere_referentiels,
    scan_unresolved_carriere_fk,
)
from sme_gend.services.mysql_import_period import (
    refresh_carriere_segment_dates,
    refresh_est_actuelle_flags,
)
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm


def _refresh_actual_month_flags(settings,conn):
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    # Repairing an archive must never activate it in place of the real open month.
    month=load_active_month_snapshot(settings).active_month
    return refresh_est_actuelle_flags(conn,month) if month else 0


def _prev_yyyy_mm(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    if m == 1:
        return f"{y - 1:04d}-12"
    return f"{y:04d}-{m - 1:02d}"


@dataclass(slots=True)
class SotMetricsReport:
    mois_actif: str | None = None
    fk_by_mois: list[dict[str, Any]] = field(default_factory=list)
    fk_mois_actif: dict[str, Any] | None = None
    agents_sans_actuelle: int = 0
    ecarts_diplome: int = 0
    ecarts_grade: int = 0
    ecarts_affectation: int = 0
    segments_sans_debut: int = 0
    segments_ouverts_avec_fin: int = 0
    volumes: dict[str, int] = field(default_factory=dict)
    unresolved_actif: CarriereUnresolvedCounts | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mois_actif": self.mois_actif,
            "fk_by_mois": self.fk_by_mois,
            "fk_mois_actif": self.fk_mois_actif,
            "agents_sans_actuelle": self.agents_sans_actuelle,
            "ecarts_diplome": self.ecarts_diplome,
            "ecarts_grade": self.ecarts_grade,
            "ecarts_affectation": self.ecarts_affectation,
            "segments_sans_debut": self.segments_sans_debut,
            "segments_ouverts_avec_fin": self.segments_ouverts_avec_fin,
            "volumes": self.volumes,
            "unresolved_actif": (
                {
                    "grade": self.unresolved_actif.grade,
                    "structure": self.unresolved_actif.structure,
                    "position_admin": self.unresolved_actif.position_admin,
                    "fonction": self.unresolved_actif.fonction,
                    "subdivision_arme": self.unresolved_actif.subdivision_arme,
                }
                if self.unresolved_actif
                else None
            ),
        }


def _active_mois() -> str | None:
    return canonical_sme_mois_yyyy_mm(load_sme_context().mois_actif or "") or None


def collect_sot_metrics(settings: Settings, *, mois: str | None = None) -> SotMetricsReport:
    """Exécute les requêtes de contrôle §5 CHECKLIST_CONVERGENCE_SOT."""
    report = SotMetricsReport(mois_actif=mois or _active_mois())
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  p.annee_mois,
                  SUM(c.grade_id IS NULL AND TRIM(COALESCE(c.grade_libelle,'')) <> '') AS grade_unresolved,
                  SUM(c.structure_id IS NULL AND TRIM(COALESCE(c.structure_code,'')) <> '') AS structure_unresolved,
                  SUM(c.position_admin_id IS NULL AND TRIM(COALESCE(c.position_admin_libelle,'')) <> '') AS padmin_unresolved,
                  SUM(c.fonction_id IS NULL AND TRIM(COALESCE(c.fonction_libelle,'')) <> '') AS fonction_unresolved,
                  SUM(c.subdivision_arme_id IS NULL AND TRIM(COALESCE(c.subdivision_arme_libelle,'')) <> '') AS subdiv_unresolved
                FROM carriere_situation c
                LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
                GROUP BY p.annee_mois
                ORDER BY p.annee_mois DESC
                LIMIT 12
                """
            )
            cols = [d[0] for d in cur.description]
            report.fk_by_mois = [dict(zip(cols, row)) for row in cur.fetchall()]

            ym = report.mois_actif
            if ym:
                cur.execute(
                    """
                    SELECT
                      SUM(c.grade_id IS NULL AND TRIM(COALESCE(c.grade_libelle,'')) <> '') AS grade_unresolved,
                      SUM(c.structure_id IS NULL AND TRIM(COALESCE(c.structure_code,'')) <> '') AS structure_unresolved,
                      SUM(c.position_admin_id IS NULL AND TRIM(COALESCE(c.position_admin_libelle,'')) <> '') AS padmin_unresolved,
                      SUM(c.fonction_id IS NULL AND TRIM(COALESCE(c.fonction_libelle,'')) <> '') AS fonction_unresolved,
                      SUM(c.subdivision_arme_id IS NULL AND TRIM(COALESCE(c.subdivision_arme_libelle,'')) <> '') AS subdiv_unresolved,
                      COUNT(*) AS total_lignes
                    FROM carriere_situation c
                    INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
                    """,
                    (ym,),
                )
                row = cur.fetchone()
                if row:
                    cols = [d[0] for d in cur.description]
                    report.fk_mois_actif = dict(zip(cols, row))
                report.unresolved_actif = scan_unresolved_carriere_fk(cur, mois=ym)

            cur.execute(
                """
                SELECT COUNT(*) FROM agent a
                WHERE NOT EXISTS (
                  SELECT 1 FROM carriere_situation c
                  WHERE c.agent_id = a.id AND c.est_actuelle = 1
                )
                """
            )
            report.agents_sans_actuelle = int((cur.fetchone() or [0])[0])

            cur.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM carriere_situation c
                   JOIN agent a ON a.id=c.agent_id
                   LEFT JOIN (
                     SELECT agent_id, libelle_libre,
                            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY date_obtention DESC, id DESC) rn
                     FROM agent_diplome) d ON d.agent_id=a.id AND d.rn=1
                   WHERE c.est_actuelle=1
                     AND COALESCE(TRIM(c.diplome_libelle),'') <> COALESCE(TRIM(d.libelle_libre),'')) AS ecarts_diplome,
                  (SELECT COUNT(*) FROM carriere_situation c
                   JOIN agent a ON a.id=c.agent_id
                   LEFT JOIN (
                     SELECT agent_id, grade_libelle,
                            ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY COALESCE(date_passation,'1900-01-01') DESC, id DESC) rn
                     FROM agent_passation_grade) pg ON pg.agent_id=a.id AND pg.rn=1
                   WHERE c.est_actuelle=1
                     AND COALESCE(TRIM(c.grade_libelle),'') <> COALESCE(TRIM(pg.grade_libelle),'')) AS ecarts_grade
                """
            )
            row = cur.fetchone()
            if row:
                report.ecarts_diplome = int(row[0] or 0)
                report.ecarts_grade = int(row[1] or 0)

            cur.execute(
                """
                SELECT COUNT(*) FROM carriere_situation c
                JOIN agent a ON a.id = c.agent_id
                LEFT JOIN (
                  SELECT agent_id, structure_code, fonction_libelle, subdivision_arme_libelle,
                         ROW_NUMBER() OVER (
                           PARTITION BY agent_id
                           ORDER BY COALESCE(date_affectation, '1900-01-01') DESC, id DESC
                         ) rn
                  FROM agent_affectation
                ) af ON af.agent_id = a.id AND af.rn = 1
                WHERE c.est_actuelle = 1
                  AND (
                    COALESCE(TRIM(c.structure_code), '') <> COALESCE(TRIM(af.structure_code), '')
                    OR COALESCE(TRIM(c.fonction_libelle), '') <> COALESCE(TRIM(af.fonction_libelle), '')
                    OR COALESCE(TRIM(c.subdivision_arme_libelle), '') <> COALESCE(TRIM(af.subdivision_arme_libelle), '')
                  )
                """
            )
            report.ecarts_affectation = int((cur.fetchone() or [0])[0])

            cur.execute("SELECT COUNT(*) FROM carriere_situation WHERE date_debut IS NULL")
            report.segments_sans_debut = int((cur.fetchone() or [0])[0])
            cur.execute(
                "SELECT COUNT(*) FROM carriere_situation WHERE est_actuelle = 1 AND date_fin IS NOT NULL"
            )
            report.segments_ouverts_avec_fin = int((cur.fetchone() or [0])[0])

            cur.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM agent) AS agents,
                  (SELECT COUNT(*) FROM carriere_situation) AS carrieres,
                  (SELECT COUNT(*) FROM carriere_situation WHERE est_actuelle=1) AS carrieres_actuelles,
                  (SELECT COUNT(*) FROM agent_diplome) AS diplomes,
                  (SELECT COUNT(*) FROM agent_passation_grade) AS passations,
                  (SELECT COUNT(*) FROM ref_subdivision_arme) AS ref_subdiv_arme,
                  (SELECT COUNT(*) FROM sme_periode) AS periodes
                """
            )
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                report.volumes = {k: int(v or 0) for k, v in zip(cols, row)}
    return report


@automatic_write
def resync_fk_referentiels(
    settings: Settings,
    *,
    mois: str | None = None,
) -> dict[str, Any]:
    """Backfill FK carrière (incl. subdiv organisationnelle auto_create)."""
    ym = (mois or _active_mois() or "").strip() or None
    result = reconcile_carriere_referentiels(settings, mois=ym, log_unresolved=False)
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        refresh_carriere_segment_dates(conn)
        conn.commit()
    return result.to_summary_dict()


@automatic_write
def project_carriere_from_dossier_batch(
    settings: Settings,
    *,
    mois: str | None = None,
    only_est_actuelle: bool = True,
) -> dict[str, int]:
    """Projette diplôme / grade / affectation dossier → carriere_situation."""
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db, load_subdiv_flat_rows

    ym = (mois or _active_mois() or "").strip() or None
    repo = MySQLAgentDossierRepository(settings)
    mc = MySQLConnection(settings)
    projected = 0
    with mc.connect() as conn:
        with conn.cursor() as cur:
            if only_est_actuelle:
                cur.execute(
                    "SELECT id, agent_id FROM carriere_situation WHERE est_actuelle = 1 ORDER BY id"
                )
            elif ym:
                from sme_gend.config.liste_generale_scope import carriere_scope_sql

                scope_sql, scope_params = carriere_scope_sql(ym, alias="c")
                cur.execute(
                    f"SELECT c.id, c.agent_id FROM carriere_situation c "
                    f"WHERE {scope_sql} ORDER BY c.id",
                    scope_params,
                )
            else:
                cur.execute("SELECT id, agent_id FROM carriere_situation ORDER BY id")
            rows = cur.fetchall()
            # Chargé une seule fois pour tout le lot (au lieu d'une relecture complète
            # du référentiel par agent) : sur plusieurs centaines d'agents, c'était le
            # principal facteur d'un import qui pouvait prendre des dizaines de minutes.
            ref_cache = load_ref_cache_from_db(cur)
            flat_rows = load_subdiv_flat_rows(cur)
        for cid, aid in rows:
            repo.sync_carriere_from_dossier(
                int(aid), str(int(cid)), ref_cache=ref_cache, flat_rows=flat_rows
            )
            projected += 1
    return {"projected": projected, "mois": ym}


@automatic_write
def carry_forward_orphan_carriere(
    settings: Settings,
    *,
    mois: str | None = None,
    policy: str = "all",
) -> dict[str, Any]:
    """
    Crée un segment ``carriere_situation`` pour le mois cible à partir du dernier
    segment connu, pour les agents sans ligne sur ce mois.

    ``policy`` :
    - ``adjacent`` : uniquement si le dernier segment est le mois précédent ;
    - ``all`` : report depuis le dernier segment quel que soit l'écart.
    """
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.mysql_import_period import (
        ensure_sme_periode,
        segment_dates_for_sme_mois,
    )

    ym = canonical_sme_mois_yyyy_mm((mois or _active_mois() or "").strip())
    if not ym:
        raise ValueError("Mois cible invalide (attendu YYYY-MM).")
    if policy not in ("adjacent", "all"):
        raise ValueError("policy doit valoir 'adjacent' ou 'all'.")

    prev_ym = _prev_yyyy_mm(ym)
    mc = MySQLConnection(settings)
    created = 0
    skipped = 0
    by_source: dict[str, int] = {}
    new_carriere_ids: list[int] = []

    with mc.connect() as conn:
        with conn.cursor() as cur:
            periode_id = ensure_sme_periode(conn, ym)
            date_debut, date_fin = segment_dates_for_sme_mois(ym, open_segment=True)
            cur.execute(
                """
                SELECT latest.agent_id, latest.src_id, latest.src_mois
                FROM (
                  SELECT c1.agent_id, c1.id AS src_id, p1.annee_mois AS src_mois,
                         ROW_NUMBER() OVER (
                           PARTITION BY c1.agent_id
                           ORDER BY COALESCE(p1.annee_mois, '0000-00') DESC, c1.id DESC
                         ) rn
                  FROM carriere_situation c1
                  LEFT JOIN sme_periode p1 ON p1.id = c1.sme_periode_id
                  WHERE p1.annee_mois < %s OR p1.annee_mois IS NULL
                ) latest
                WHERE latest.rn = 1
                  AND NOT EXISTS (
                    SELECT 1 FROM carriere_situation cx
                    INNER JOIN sme_periode px ON px.id = cx.sme_periode_id
                    WHERE cx.agent_id = latest.agent_id AND px.annee_mois = %s
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM mouvement_effectif m
                    INNER JOIN sme_periode pm ON pm.id = m.sme_periode_id
                    WHERE m.agent_id = latest.agent_id
                      AND UPPER(COALESCE(m.categorie_mouvement, m.type_mouvement, '')) = 'PERTE'
                      AND COALESCE(pm.annee_mois, '0000-00') >= COALESCE(latest.src_mois, '0000-00')
                      AND pm.annee_mois <= %s
                  )
                """,
                (ym,ym,ym),
            )
            for agent_id, src_id, src_mois in cur.fetchall():
                src_label = str(src_mois or "sans_periode")
                if policy == "adjacent" and src_label != prev_ym:
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO carriere_situation (
                      agent_id, sme_periode_id, est_actuelle,
                      grade_libelle, date_grade, date_service, ndecision,
                      position_admin_libelle, fonction_libelle,
                      subdivision_arme_libelle, diplome_libelle, date_obtention,
                      structure_code, obs, ordre_tri, presseance,
                      structure_id, grade_id, position_admin_id, fonction_id,
                      subdivision_arme_id, import_lot_id, date_debut, date_fin
                    )
                    SELECT
                      agent_id, %s, 0,
                      grade_libelle, date_grade, date_service, ndecision,
                      position_admin_libelle, fonction_libelle,
                      subdivision_arme_libelle, diplome_libelle, date_obtention,
                      structure_code, obs, ordre_tri, presseance,
                      structure_id, grade_id, position_admin_id, fonction_id,
                      subdivision_arme_id, import_lot_id, %s, %s
                    FROM carriere_situation
                    WHERE id = %s
                    """,
                    (periode_id, date_debut, date_fin, int(src_id)),
                )
                new_id = int(cur.lastrowid)
                new_carriere_ids.append(new_id)
                created += 1
                by_source[src_label] = by_source.get(src_label, 0) + 1
            flagged = _refresh_actual_month_flags(settings,conn)
            refresh_carriere_segment_dates(conn)
    return {
        "mois": ym,
        "policy": policy,
        "segments_created": created,
        "skipped": skipped,
        "est_actuelle_set": flagged,
        "by_source_mois": by_source,
        "new_carriere_ids": new_carriere_ids,
    }


@automatic_write
def revert_stale_carry_forward(
    settings: Settings,
    *,
    mois: str | None = None,
    keep_from_mois: str = "2026-04",
) -> dict[str, Any]:
    """
    Supprime les segments ``carriere_situation`` du mois cible créés par report
    depuis un historique trop ancien (sorties / décès / reliquats).

    Conserve les segments dont le dernier mois connu avant le cible est
    ``>= keep_from_mois`` (ex. bascule avril → mai).
    """
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    ym = canonical_sme_mois_yyyy_mm((mois or _active_mois() or "").strip())
    keep_from = canonical_sme_mois_yyyy_mm((keep_from_mois or "").strip())
    if not ym:
        raise ValueError("Mois cible invalide (attendu YYYY-MM).")
    if not keep_from:
        raise ValueError("keep_from_mois invalide (attendu YYYY-MM).")

    mc = MySQLConnection(settings)
    removed = 0
    kept = 0
    by_prev: dict[str, int] = {}

    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.agent_id,
                       (
                         SELECT MAX(COALESCE(p2.annee_mois, '0000-00'))
                         FROM carriere_situation c2
                         LEFT JOIN sme_periode p2 ON p2.id = c2.sme_periode_id
                         WHERE c2.agent_id = c.agent_id AND c2.id <> c.id
                       ) AS prev_mois
                FROM carriere_situation c
                INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
                """,
                (ym,),
            )
            for cid, _aid, prev_mois in cur.fetchall():
                prev = str(prev_mois or "0000-00")
                if prev >= keep_from:
                    kept += 1
                    continue
                cur.execute("DELETE FROM carriere_situation WHERE id = %s", (int(cid),))
                removed += 1
                key = prev if prev != "0000-00" else "sans_periode"
                by_prev[key] = by_prev.get(key, 0) + 1
            flagged = _refresh_actual_month_flags(settings,conn)
            refresh_carriere_segment_dates(conn)
    return {
        "mois": ym,
        "keep_from_mois": keep_from,
        "segments_removed": removed,
        "segments_kept": kept,
        "est_actuelle_set": flagged,
        "removed_by_prev_mois": by_prev,
    }


@automatic_write
def trim_effectif_to_import_batch(
    settings: Settings,
    *,
    mois: str | None = None,
) -> dict[str, Any]:
    """
    Supprime les segments du mois cible ajoutés après le lot d'import principal
    (reports automatiques post-import).

    Retient la date ``cree_le`` la plus fréquente sur le mois ; efface les lignes
    créées un jour ultérieur.
    """
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    ym = canonical_sme_mois_yyyy_mm((mois or _active_mois() or "").strip())
    if not ym:
        raise ValueError("Mois cible invalide (attendu YYYY-MM).")

    mc = MySQLConnection(settings)
    removed = 0
    removed_agents: list[dict[str, Any]] = []

    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DATE(c.cree_le) AS d, COUNT(*) AS n
                FROM carriere_situation c
                INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
                GROUP BY DATE(c.cree_le)
                ORDER BY n DESC, d ASC
                LIMIT 1
                """,
                (ym,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return {"mois": ym, "segments_removed": 0, "import_batch_date": None}
            import_day = row[0]

            cur.execute(
                """
                SELECT c.id, c.agent_id, a.noms, a.legacy_access_id, c.cree_le
                FROM carriere_situation c
                INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
                INNER JOIN agent a ON a.id = c.agent_id
                WHERE DATE(c.cree_le) > %s
                ORDER BY c.id
                """,
                (ym, import_day),
            )
            for cid, aid, noms, leg, cree in cur.fetchall():
                cur.execute("DELETE FROM carriere_situation WHERE id = %s", (int(cid),))
                removed += 1
                removed_agents.append(
                    {
                        "carriere_id": int(cid),
                        "agent_id": int(aid),
                        "legacy_access_id": leg,
                        "noms": noms,
                        "cree_le": str(cree),
                    }
                )
            flagged = _refresh_actual_month_flags(settings,conn)
            refresh_carriere_segment_dates(conn)
            cur.execute(
                """
                SELECT COUNT(*) FROM carriere_situation c
                INNER JOIN sme_periode p ON p.id = c.sme_periode_id AND p.annee_mois = %s
                WHERE c.est_actuelle = 1
                """,
                (ym,),
            )
            effectif = int((cur.fetchone() or [0])[0])

    return {
        "mois": ym,
        "import_batch_date": str(import_day),
        "segments_removed": removed,
        "removed_agents": removed_agents,
        "est_actuelle_set": flagged,
        "effectif_actuel": effectif,
    }


def _project_carriere_ids(
    settings: Settings,
    rows: list[tuple[int, int]],
) -> int:
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db, load_subdiv_flat_rows

    if not rows:
        return 0
    repo = MySQLAgentDossierRepository(settings)
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            ref_cache = load_ref_cache_from_db(cur)
            flat_rows = load_subdiv_flat_rows(cur)
    for cid, aid in rows:
        repo.sync_carriere_from_dossier(
            int(aid), str(int(cid)), ref_cache=ref_cache, flat_rows=flat_rows
        )
    return len(rows)


@automatic_write
def purge_phantom_carriere_segments(settings: Settings) -> dict[str, Any]:
    """
    Supprime les segments carrière sans ``sme_periode_id`` et ``est_actuelle = 0``.

    Reliquats de maintenance / carry-forward qui ne doivent pas apparaître dans
    l'effectif ni dans les requêtes par intervalle de dates.
    """
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    mc = MySQLConnection(settings)
    removed: list[dict[str, Any]] = []
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.agent_id, a.legacy_access_id, a.noms, c.date_debut
                FROM carriere_situation c
                INNER JOIN agent a ON a.id = c.agent_id
                WHERE c.est_actuelle = 0
                  AND c.sme_periode_id IS NULL
                  AND (
                    EXISTS (
                      SELECT 1 FROM carriere_situation c2
                      WHERE c2.agent_id = c.agent_id
                        AND c2.sme_periode_id IS NOT NULL
                        AND c2.id <> c.id
                    )
                    OR NOT EXISTS (
                      SELECT 1 FROM carriere_situation c3
                      WHERE c3.agent_id = c.agent_id AND c3.est_actuelle = 1
                    )
                  )
                ORDER BY c.id
                """
            )
            for cid, aid, leg, noms, db in cur.fetchall():
                cur.execute("DELETE FROM carriere_situation WHERE id = %s", (int(cid),))
                removed.append(
                    {
                        "carriere_id": int(cid),
                        "agent_id": int(aid),
                        "legacy_access_id": leg,
                        "noms": noms,
                        "date_debut": str(db) if db else None,
                    }
                )
            refresh_carriere_segment_dates(conn)
            conn.commit()
    return {"segments_removed": len(removed), "removed_agents": removed}


@automatic_write
def fix_agents_sans_est_actuelle(
    settings: Settings,
    *,
    mois: str | None = None,
    policy: str = "all",
    project_dossier: bool = True,
) -> dict[str, Any]:
    """Report carrière orphelins + réalignement est_actuelle + projection dossier."""
    ym = (mois or _active_mois() or "").strip() or None
    out: dict[str, Any] = {
        "carry_forward": carry_forward_orphan_carriere(
            settings, mois=ym, policy=policy
        ),
    }
    if project_dossier:
        new_ids = out["carry_forward"].get("new_carriere_ids") or []
        if new_ids:
            mc = MySQLConnection(settings)
            with mc.connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    placeholders = ",".join(["%s"] * len(new_ids))
                    cur.execute(
                        f"SELECT id, agent_id FROM carriere_situation WHERE id IN ({placeholders})",
                        new_ids,
                    )
                    rows = [(int(r[0]), int(r[1])) for r in cur.fetchall()]
            out["dossier_projection"] = {
                "projected": _project_carriere_ids(settings, rows),
                "mois": ym,
            }
        else:
            out["dossier_projection"] = {"projected": 0, "mois": ym}
    out["fk"] = resync_fk_referentiels(settings, mois=ym)
    out["metrics"] = collect_sot_metrics(settings, mois=ym).to_dict()
    return out


@automatic_write
def run_full_sot_resync(
    settings: Settings,
    *,
    mois: str | None = None,
    project_dossier: bool = True,
) -> dict[str, Any]:
    """FK + dates + projection dossier."""
    out: dict[str, Any] = {"fk": resync_fk_referentiels(settings, mois=mois)}
    if project_dossier:
        ym = (mois or _active_mois() or "").strip() or None
        out["dossier_projection"] = project_carriere_from_dossier_batch(
            settings,
            mois=ym,
            only_est_actuelle=not bool(ym),
        )
    out["metrics"] = collect_sot_metrics(settings, mois=mois).to_dict()
    return out
