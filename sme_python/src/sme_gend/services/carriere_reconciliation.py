"""Réconciliation carrière ↔ référentiels (FK, ordre_tri / préséance)."""

from __future__ import annotations

from sme_gend.services.automatic_maintenance import automatic_write

from dataclasses import dataclass, field
from typing import Any
from functools import wraps

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_common import ensure_mysql_schema
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.etl_erreur_service import log_etl_erreur
from sme_gend.services.mysql_import_period import ensure_mysql_migrations
from sme_gend.services.mysql_ref_backfill import backfill_carriere_fk_and_dates
from sme_gend.services.mysql_ref_etl import RefEtlCache, ensure_ref_etl_cache, load_ref_cache_from_db
from sme_gend.services.mysql_ref_etl import _cf as _casefold_key
from sme_gend.padmin_present import invalidate_padmin_presence_cache
from sme_gend.services.operation_progress import operation_progress, current_operation_progress
from sme_gend.services.operation_commit_progress import finish_after_commit


_RECONCILIATION_STEPS = (
    ('prepare', 'Préparation et verrouillage des référentiels'),
    ('before', 'Analyse des liens manquants'),
    ('references', 'Réconciliation des situations carrière'),
    ('precedence', 'Alignement de l’ordre des grades'),
    ('after', 'Contrôle des liens après réconciliation'),
    ('journal', 'Journalisation des anomalies restantes'),
    ('commit', 'Validation des corrections dans MySQL'),
)


def _reconciliation_progress(function):
    @wraps(function)
    def run(*args, **kwargs):
        with operation_progress(_RECONCILIATION_STEPS) as progress:
            progress.start('prepare', detail='Contrôle du plan et acquisition de la transaction…')
            result = function(*args, **kwargs)
            finish_after_commit(args[0] if args else kwargs['settings'], progress, 'commit',
                                detail='Réconciliation terminée ; transaction validée.')
            return result
    return run


@dataclass(slots=True)
class CarriereUnresolvedCounts:
    grade: int = 0
    structure: int = 0
    position_admin: int = 0
    fonction: int = 0
    subdivision_arme: int = 0

    def total(self) -> int:
        return (
            self.grade
            + self.structure
            + self.position_admin
            + self.fonction
            + self.subdivision_arme
        )


@dataclass(slots=True)
class CarriereReconciliationResult:
    fk_rows_updated: int = 0
    ordre_tri_updated: int = 0
    unresolved_before: CarriereUnresolvedCounts = field(default_factory=CarriereUnresolvedCounts)
    unresolved_after: CarriereUnresolvedCounts = field(default_factory=CarriereUnresolvedCounts)
    etl_errors_logged: int = 0
    mois: str | None = None

    def to_summary_dict(self) -> dict[str, int | str | None]:
        """Résumé sérialisable pour audit / rapport d'import."""
        return {
            "mois": self.mois,
            "fk_updated": self.fk_rows_updated,
            "ordre_tri_updated": self.ordre_tri_updated,
            "unresolved_before": self.unresolved_before.total(),
            "unresolved_after": self.unresolved_after.total(),
            "etl_errors_logged": self.etl_errors_logged,
        }


def _scope_sql(mois: str | None, *, alias: str = "c") -> tuple[str, tuple]:
    ym = (mois or "").strip()
    a = (alias or "").strip()
    col = f"{a}." if a else ""
    if ym:
        return (
            f"{col}sme_periode_id = (SELECT id FROM sme_periode WHERE annee_mois = %s LIMIT 1)",
            (ym,),
        )
    return ("1=1", ())


def scan_unresolved_carriere_fk(cur: Any, *, mois: str | None = None) -> CarriereUnresolvedCounts:
    scope, params = _scope_sql(mois)
    counts = CarriereUnresolvedCounts()

    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE {scope}
          AND TRIM(COALESCE(c.grade_libelle, '')) <> ''
          AND c.grade_id IS NULL
        """,
        params,
    )
    counts.grade = int((cur.fetchone() or [0])[0])

    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE {scope}
          AND TRIM(COALESCE(c.structure_code, '')) <> ''
          AND c.structure_id IS NULL
        """,
        params,
    )
    counts.structure = int((cur.fetchone() or [0])[0])

    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE {scope}
          AND TRIM(COALESCE(c.position_admin_libelle, '')) <> ''
          AND c.position_admin_id IS NULL
        """,
        params,
    )
    counts.position_admin = int((cur.fetchone() or [0])[0])

    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE {scope}
          AND TRIM(COALESCE(c.fonction_libelle, '')) <> ''
          AND c.fonction_id IS NULL
        """,
        params,
    )
    counts.fonction = int((cur.fetchone() or [0])[0])

    cur.execute(
        f"""
        SELECT COUNT(*) FROM carriere_situation c
        WHERE {scope}
          AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
          AND c.subdivision_arme_id IS NULL
        """,
        params,
    )
    counts.subdivision_arme = int((cur.fetchone() or [0])[0])

    return counts


def sync_ordre_tri_from_pref_ord(cur: Any, cache: RefEtlCache, *, mois: str | None = None) -> int:
    """Aligne ordre_tri et presseance sur ref_grade.pref_ord pour les grades connus."""
    scope, params = _scope_sql(mois)
    cur.execute(
        f"""
        SELECT c.id, c.grade_libelle FROM carriere_situation c
        WHERE {scope}
        """,
        params,
    )
    careers = cur.fetchall()
    pref_by_id: dict[int, int] = {}
    cur.execute("SELECT id, pref_ord FROM ref_grade WHERE pref_ord IS NOT NULL")
    for rid, po in cur.fetchall():
        pref_by_id[int(rid)] = int(po)

    updated = 0
    with operation_progress((('grades', 'Alignement de l’ordre des grades'),)) as progress:
        progress.start('grades', total=len(careers), detail=f'{len(careers)} situation(s) à examiner.')
        for index, (cid, gl) in enumerate(careers, 1):
            updated += _sync_career_order(cur, cache, pref_by_id, cid, gl)
            if index % 100 == 0 or index == len(careers):
                progress.update('grades', current=index, total=len(careers),
                                detail=f'{index}/{len(careers)} situation(s) examinée(s) ; {updated} ordre(s) aligné(s).')
        progress.finish('grades', detail=f'{updated} ordre(s) de grade aligné(s).')
    return updated


def _sync_career_order(cur, cache, pref_by_id, cid, gl):
    gtxt = (gl or "").strip()
    if not gtxt:
        return 0
    gid = cache.grade_by_libelle.get(_casefold_key(gtxt)) or cache.grade_by_code.get(_casefold_key(gtxt))
    if gid is None:
        return 0
    po = pref_by_id.get(int(gid))
    if po is None:
        return 0
    cur.execute("UPDATE carriere_situation SET ordre_tri=%s, presseance=%s WHERE id=%s",
                (po, po, int(cid)))
    return 1


def _log_unresolved_samples(
    cur: Any,
    *,
    import_lot_id: int | None,
    mois: str | None,
    limit: int = 200,
) -> int:
    scope, params = _scope_sql(mois)
    logged = 0

    def _log_rows(sql: str, table: str, msg_prefix: str) -> None:
        nonlocal logged
        if logged >= limit:
            return
        cur.execute(sql, params)
        for cid, leg, label in cur.fetchall():
            if logged >= limit:
                break
            log_etl_erreur(
                cur,
                import_lot_id=import_lot_id,
                table_cible=table,
                cle_legacy=str(leg or cid),
                message=f"{msg_prefix}: {label!r} (carriere id={cid})",
            )
            logged += 1

    _log_rows(
        f"""
        SELECT c.id, a.legacy_access_id, c.grade_libelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE {scope}
          AND TRIM(COALESCE(c.grade_libelle, '')) <> ''
          AND c.grade_id IS NULL
        LIMIT 50
        """,
        "carriere_situation",
        "Grade non résolu dans ref_grade",
    )
    _log_rows(
        f"""
        SELECT c.id, a.legacy_access_id, c.structure_code
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE {scope}
          AND TRIM(COALESCE(c.structure_code, '')) <> ''
          AND c.structure_id IS NULL
        LIMIT 50
        """,
        "carriere_situation",
        "Structure non résolue dans ref_structure",
    )
    _log_rows(
        f"""
        SELECT c.id, a.legacy_access_id, c.position_admin_libelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE {scope}
          AND TRIM(COALESCE(c.position_admin_libelle, '')) <> ''
          AND c.position_admin_id IS NULL
        LIMIT 50
        """,
        "carriere_situation",
        "Position admin non résolue dans ref_position_admin",
    )
    _log_rows(
        f"""
        SELECT c.id, a.legacy_access_id, c.fonction_libelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE {scope}
          AND TRIM(COALESCE(c.fonction_libelle, '')) <> ''
          AND c.fonction_id IS NULL
        LIMIT 50
        """,
        "carriere_situation",
        "Fonction non résolue dans ref_fonction",
    )
    _log_rows(
        f"""
        SELECT c.id, a.legacy_access_id, c.subdivision_arme_libelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        WHERE {scope}
          AND TRIM(COALESCE(c.subdivision_arme_libelle, '')) <> ''
          AND c.subdivision_arme_id IS NULL
        LIMIT 50
        """,
        "carriere_situation",
        "Subdivision d'arme non résolue dans ref_subdivision_arme",
    )
    return logged


def backfill_carriere_fk_scoped(
    cur: Any,
    cache: RefEtlCache,
    *,
    mois: str | None = None,
) -> int:
    if not (mois or "").strip():
        stats = backfill_carriere_fk_and_dates(cur, cache)
        return int(stats.get("carriere_fk") or 0)

    from sme_gend.services.mysql_ref_backfill import _row_from_carriere
    from sme_gend.services.mysql_ref_etl import load_subdiv_flat_rows, resolve_carriere_fk_ids

    scope, params = _scope_sql(mois)
    flat = load_subdiv_flat_rows(cur)
    cur.execute(
        f"""
        SELECT c.id, a.legacy_access_id, a.noms, a.date_naissance, a.lieu_naissance,
               c.grade_libelle, c.date_grade, c.date_service, c.ndecision,
               a.matricule_militaire, a.matricule_solde, c.position_admin_libelle,
               c.fonction_libelle, c.subdivision_arme_libelle, c.diplome_libelle,
               c.date_obtention, c.structure_code, c.obs, c.ordre_tri, c.presseance,
               c.sme_periode_id, p.annee_mois, c.est_actuelle
        FROM carriere_situation c
        INNER JOIN agent a ON a.id = c.agent_id
        LEFT JOIN sme_periode p ON p.id = c.sme_periode_id
        WHERE {scope}
        """,
        params,
    )
    rows = cur.fetchall()
    n = 0
    with operation_progress((('careers', 'Traitement des situations carrière du mois'),)) as progress:
        progress.start('careers', total=len(rows), detail=f'{len(rows)} situation(s) à réconcilier.')
        for index, r in enumerate(rows, 1):
            n += _backfill_scoped_row(cur, cache, flat, r)
            if index % 100 == 0 or index == len(rows):
                progress.update('careers', current=index, total=len(rows),
                                detail=f'{index}/{len(rows)} situation(s) réconciliée(s).')
        progress.finish('careers', detail=f'{n} situation(s) réconciliée(s).')
    return n


def _backfill_scoped_row(cur, cache, flat, r):
    from sme_gend.services.mysql_ref_backfill import _row_from_carriere
    from sme_gend.services.mysql_ref_etl import resolve_carriere_fk_ids
    # The per-row business statements are unchanged; only their progress is batched.
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
        (fk["structure_id"], fk["grade_id"], fk["position_admin_id"],
         fk["fonction_id"], fk["subdivision_arme_id"], cid),
    )
    return 1


@_reconciliation_progress
@automatic_write
def reconcile_carriere_referentiels(
    settings: Settings,
    *,
    mois: str | None = None,
    import_lot_id: int | None = None,
    log_unresolved: bool = True,
) -> CarriereReconciliationResult:
    ensure_mysql_schema(settings)
    ensure_mysql_migrations(settings)
    result = CarriereReconciliationResult(mois=(mois or "").strip() or None)
    progress = current_operation_progress()
    progress.finish('prepare', detail='Plan et schéma contrôlés ; transaction acquise.')

    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            progress.start('before', detail='Comptage des liens manquants vers les cinq référentiels…')
            result.unresolved_before = scan_unresolved_carriere_fk(cur, mois=mois)
            progress.finish('before', detail=f'{result.unresolved_before.total()} lien(s) manquant(s) détecté(s).')
            progress.start('references', detail='Chargement des référentiels et réconciliation des situations…')
            cache = ensure_ref_etl_cache(cur)
            result.fk_rows_updated = backfill_carriere_fk_scoped(cur, cache, mois=mois)
            progress.finish('references', detail=f'{result.fk_rows_updated} situation(s) traitée(s).')
            progress.start('precedence', detail='Lecture des grades et alignement de leur ordre…')
            cache = load_ref_cache_from_db(cur)
            result.ordre_tri_updated = sync_ordre_tri_from_pref_ord(cur, cache, mois=mois)
            progress.finish('precedence', detail=f'{result.ordre_tri_updated} ordre(s) aligné(s).')
            progress.start('after', detail='Nouveau contrôle des cinq liens référentiels…')
            result.unresolved_after = scan_unresolved_carriere_fk(cur, mois=mois)
            progress.finish('after', detail=f'{result.unresolved_after.total()} lien(s) restant(s) à vérifier.')
            progress.start('journal', detail='Journalisation des anomalies non résolues…')
            if log_unresolved and result.unresolved_after.total() > 0:
                result.etl_errors_logged = _log_unresolved_samples(
                    cur,
                    import_lot_id=import_lot_id,
                    mois=mois,
                )
            progress.finish('journal', detail=f'{result.etl_errors_logged} anomalie(s) journalisée(s).')
        progress.start('commit', detail='Validation de la transaction et de la révision partagée…')
        conn.commit()
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    return result


def reconcile_after_import(
    settings: Settings,
    *,
    mois: str,
    import_lot_id: int | None,
) -> CarriereReconciliationResult:
    """Réconciliation obligatoire après chaque import bundle (FK + ordre_tri + journal ETL)."""
    return reconcile_carriere_referentiels(
        settings,
        mois=mois,
        import_lot_id=import_lot_id,
        log_unresolved=True,
    )
