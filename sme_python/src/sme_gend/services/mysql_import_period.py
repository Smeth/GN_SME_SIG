"""Périodes SME, lots d'import et migrations incrémentales MySQL."""

from __future__ import annotations

import calendar
import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sme_gend.config.settings import Settings
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm


def normalize_import_mois(mois: str) -> str:
    """Retourne ``YYYY-MM`` ou lève ``ValueError``."""
    ym = canonical_sme_mois_yyyy_mm((mois or "").strip())
    if not ym:
        raise ValueError(f"Mois invalide (attendu YYYY-MM) : {mois!r}")
    return ym


def last_day_of_month(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def first_day_of_month(ym: str) -> date:
    y, m = int(ym[:4]), int(ym[5:7])
    return date(y, m, 1)


def apply_drop_tab_legacy(settings: Settings) -> None:
    """Supprime les tables miroir tab_* après migration vers ref_* / agent_*."""
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    p = project_root() / "data" / "schema" / "mysql_drop_tab_legacy.sql"
    if not p.is_file():
        return
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "unknown table" in err or "1051" in err:
                        continue
                    raise


def apply_numerique_agent_schema(settings: Settings) -> None:
    """Crée agent_mission / malade / desertion et colonnes mouvement_effectif."""
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    p = project_root() / "data" / "schema" / "mysql_numerique_agent_v2.sql"
    if not p.is_file():
        return
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if (
                        "duplicate" in err
                        or "already exists" in err
                        or "1060" in err
                        or "1061" in err
                    ):
                        continue
                    raise


def apply_agent_conge_schema(settings: Settings) -> None:
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    p = project_root() / "data" / "schema" / "mysql_agent_conge.sql"
    if not p.is_file():
        return
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "duplicate" in err or "already exists" in err or "1050" in err:
                        continue
                    raise


def apply_agent_distinction_schema(settings: Settings) -> None:
    """Médailles, décorations / ordres, attributions agent_distinction."""
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    p = project_root() / "data" / "schema" / "mysql_agent_distinction.sql"
    if not p.is_file():
        return
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if (
                        "duplicate" in err
                        or "already exists" in err
                        or "1050" in err
                        or "1060" in err
                        or "1061" in err
                    ):
                        continue
                    raise
    try:
        from sme_gend.repositories.mysql_distinction_repository import (
            MySQLDistinctionRepository,
        )

        MySQLDistinctionRepository(settings).seed_defaults_if_empty()
    except Exception as exc:
        raise RuntimeError("Initialisation du référentiel des distinctions impossible.") from exc


def apply_avancement_schema(settings: Settings) -> None:
    """Initialize only the active rule table, without rewriting stored rules.

    Legacy campaign/quota/nomination tables remain available to old backups,
    but are neither migrated nor queried by the active Avancement module.
    Defaults are overlaid in memory; explicit versioned actions persist changes.
    """
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements

    path = project_root() / "data" / "schema" / "mysql_avancement.sql"
    statements = _split_sql_statements(path.read_text(encoding="utf-8"))
    active = [statement for statement in statements
        if statement.lstrip().upper().startswith("CREATE TABLE IF NOT EXISTS AVANCEMENT_REGLE (")]
    if len(active) != 1:
        raise RuntimeError("Schéma du barème Avancement indisponible ; aucune règle n'a été remplacée.")
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute(active[0])



def apply_agent_dossier_schema(settings: Settings) -> None:
    """Diplômes multi-lignes + passations de grade."""
    mc = MySQLConnection(settings)
    stmts = [
        """
        CREATE TABLE IF NOT EXISTS agent_passation_grade (
          id BIGINT NOT NULL AUTO_INCREMENT,
          agent_id BIGINT NOT NULL,
          grade_id BIGINT DEFAULT NULL,
          grade_libelle VARCHAR(256) NOT NULL,
          date_passation DATE DEFAULT NULL,
          ndecision VARCHAR(128) DEFAULT NULL,
          obs TEXT,
          PRIMARY KEY (id),
          KEY idx_passation_agent_date (agent_id, date_passation),
          CONSTRAINT fk_passation_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
          CONSTRAINT fk_passation_grade FOREIGN KEY (grade_id) REFERENCES ref_grade (id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS agent_affectation (
          id BIGINT NOT NULL AUTO_INCREMENT,
          agent_id BIGINT NOT NULL,
          structure_code VARCHAR(64) NOT NULL,
          structure_id BIGINT DEFAULT NULL,
          fonction_libelle VARCHAR(512) DEFAULT NULL,
          subdivision_arme_libelle VARCHAR(256) DEFAULT NULL,
          date_affectation DATE NOT NULL,
          sme_periode_mois VARCHAR(7) DEFAULT NULL,
          obs TEXT,
          import_lot_id BIGINT DEFAULT NULL,
          PRIMARY KEY (id),
          KEY idx_affectation_agent_date (agent_id, date_affectation),
          CONSTRAINT fk_affectation_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        ALTER TABLE agent_diplome
        ADD COLUMN categorie VARCHAR(32) DEFAULT NULL COMMENT 'MILITAIRE ou CIVIL'
        """,
    ]
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in stmts:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "duplicate" in err or "already exists" in err or "1060" in err or "1061" in err:
                        continue
                    raise


def _apply_structure_organigram_schema(settings: Settings) -> None:
    from sme_gend.config.settings import project_root
    from sme_gend.repositories.mysql_common import _split_sql_statements
    from sme_gend.repositories.mysql_connection import MySQLConnection

    p = project_root() / "data" / "schema" / "mysql_structure_organigram.sql"
    if not p.is_file():
        return
    sql = p.read_text(encoding="utf-8")
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in _split_sql_statements(sql):
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if "duplicate" in err or "already exists" in err:
                        continue
                    raise


def ensure_mysql_migrations(settings: Settings) -> None:
    """Applique les migrations sous un verrou partagé entre les postes clients."""
    from sme_gend.repositories.mysql_connection import has_import_transaction, mysql_initialization_lock

    if has_import_transaction(settings):
        # Les migrations ont été appliquées avant la transaction : du DDL ici
        # provoquerait un commit implicite de l'effacement du mois.
        return

    with mysql_initialization_lock(settings):
        _ensure_mysql_migrations_unlocked(settings)


def _ensure_mysql_migrations_unlocked(settings: Settings) -> None:
    """Applique les ALTER TABLE idempotents (historique, stages par période, config app)."""
    from sme_gend.repositories.app_config_repository import AppConfigRepository

    try:
        AppConfigRepository(settings).ensure_tables()
    except Exception:
        pass
    from sme_gend.services.agent_identity_alias import apply_identity_alias_schema
    apply_identity_alias_schema(settings)
    apply_numerique_agent_schema(settings)
    apply_agent_conge_schema(settings)
    from sme_gend.services.position_admin_events import apply_position_event_schema
    apply_position_event_schema(settings)
    apply_agent_dossier_schema(settings)
    apply_agent_distinction_schema(settings)
    apply_avancement_schema(settings)
    apply_drop_tab_legacy(settings)
    _apply_structure_organigram_schema(settings)
    mc = MySQLConnection(settings)
    alters = [
        "ALTER TABLE agent_desertion ADD COLUMN reference_dossier VARCHAR(256) DEFAULT NULL",
        "ALTER TABLE parcours_stage ADD COLUMN session VARCHAR(256) DEFAULT NULL",
        """
        ALTER TABLE agent_diplome
        ADD KEY idx_agent_diplome_agent (agent_id, date_obtention)
        """,
        """
        ALTER TABLE carriere_situation
        ADD UNIQUE KEY uk_carriere_agent_periode (agent_id, sme_periode_id)
        """,
        """
        ALTER TABLE parcours_stage
        ADD COLUMN sme_periode_id BIGINT DEFAULT NULL AFTER agent_id
        """,
        """
        ALTER TABLE parcours_stage
        ADD KEY idx_parcours_periode (sme_periode_id),
        ADD CONSTRAINT fk_parcours_periode
          FOREIGN KEY (sme_periode_id) REFERENCES sme_periode (id)
        """,
        """
        ALTER TABLE carriere_situation
        ADD COLUMN cree_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        """,
        """
        ALTER TABLE carriere_situation
        ADD COLUMN updated_le DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
          ON UPDATE CURRENT_TIMESTAMP
        """,
        """
        ALTER TABLE agent_mission
        ADD UNIQUE KEY uk_agent_mission_periode_legacy (agent_id, sme_periode_id, source_ligne_legacy)
        """,
        """
        ALTER TABLE agent_malade
        ADD UNIQUE KEY uk_agent_malade_periode_legacy (agent_id, sme_periode_id, source_ligne_legacy)
        """,
        """
        ALTER TABLE carriere_situation
        ADD KEY idx_carriere_periode (sme_periode_id)
        """,
        """
        ALTER TABLE agent_mission
        ADD KEY idx_agent_mission_periode (sme_periode_id, agent_id)
        """,
        """
        ALTER TABLE agent_malade
        ADD KEY idx_agent_malade_periode (sme_periode_id, agent_id)
        """,
        """
        ALTER TABLE parcours_stage
        ADD KEY idx_parcours_stage_periode (sme_periode_id, agent_id)
        """,
        """
        ALTER TABLE agent
        ADD COLUMN sexe CHAR(1) DEFAULT NULL COMMENT 'M = masculin, F = féminin'
          AFTER lieu_naissance
        """,
        """
        ALTER TABLE agent
        ADD COLUMN telephone VARCHAR(64) DEFAULT NULL AFTER sexe
        """,
        """
        ALTER TABLE agent
        ADD COLUMN adresse_residence VARCHAR(512) DEFAULT NULL AFTER telephone
        """,
        """
        ALTER TABLE carriere_situation
        ADD KEY idx_carriere_fonction (fonction_id)
        """,
        """
        ALTER TABLE ref_structure
        ADD COLUMN famille_unite VARCHAR(64) DEFAULT NULL
          COMMENT 'Typologie unité : DIRECTION, REGION, ECOLE…'
          AFTER ordre_tri
        """,
    ]
    with mc.connect() as conn:
        with conn.cursor() as cur:
            for stmt in alters:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    err = str(e).lower()
                    if (
                        "duplicate" in err
                        or "already exists" in err
                        or "1061" in err
                        or "1060" in err
                        or "1826" in err
                    ):
                        continue
                    raise


def ensure_sme_periode(
    conn: Any,
    mois: str,
    *,
    codsme: str | None = None,
    statut: str | None = None,
) -> int:
    """Crée ou met à jour ``sme_periode`` ; retourne l'id."""
    ym = normalize_import_mois(mois)
    fin = last_day_of_month(ym)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM sme_periode WHERE annee_mois = %s", (ym,))
        row = cur.fetchone()
        if row:
            pid = int(row[0])
            cur.execute(
                """
                UPDATE sme_periode
                SET date_dernier_jour_mois = %s, codsme = COALESCE(%s, codsme),
                    statut = COALESCE(%s, statut)
                WHERE id = %s
                """,
                (fin, codsme, statut, pid),
            )
            return pid
        cur.execute(
            """
            INSERT INTO sme_periode (annee_mois, date_dernier_jour_mois, codsme, statut)
            VALUES (%s, %s, %s, %s)
            """,
            (ym, fin, codsme, statut),
        )
        return int(cur.lastrowid)


def create_import_lot(
    conn: Any,
    sme_periode_id: int,
    chemin_fichier: str | Path | None,
) -> int:
    path = str(chemin_fichier or "").strip()
    checksum = ""
    if path:
        try:
            data = Path(path).read_bytes()
            checksum = hashlib.sha256(data).hexdigest()[:64]
        except OSError:
            checksum = ""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_lot (sme_periode_id, chemin_fichier, checksum)
            VALUES (%s, %s, %s)
            """,
            (sme_periode_id, path or None, checksum or None),
        )
        return int(cur.lastrowid)


def dedupe_carriere_for_periode(conn: Any, sme_periode_id: int) -> int:
    """
    Supprime les doublons ``carriere_situation`` (même agent + même période).
    Conserve la ligne la plus récente (``id`` max).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE c FROM carriere_situation c
            INNER JOIN carriere_situation c2
              ON c.agent_id = c2.agent_id
             AND c.sme_periode_id = c2.sme_periode_id
             AND c.id < c2.id
            WHERE c.sme_periode_id = %s
            """,
            (sme_periode_id,),
        )
        return int(cur.rowcount or 0)


def repair_carriere_liste_duplicates(settings: Settings) -> dict[str, int]:
    """
    Nettoie les doublons ``carriere_situation`` et réaligne ``est_actuelle`` /
    ``date_debut`` / ``date_fin``. À lancer après un double import accidentel.
    """
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.sme_workspace_service import SmeWorkspaceService

    removed = 0
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT sme_periode_id FROM carriere_situation")
            for (pid,) in cur.fetchall():
                if pid is not None:
                    removed += dedupe_carriere_for_periode(conn, int(pid))
        actif = SmeWorkspaceService(settings).get_active_mois()
        refresh_est_actuelle_flags(conn, actif)
        refresh_carriere_segment_dates(conn)
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    invalidate_liste_cache(settings)
    return {"carriere_rows_removed": removed}


def refresh_est_actuelle_flags(conn: Any, mois_actif: str | None = None) -> int:
    """
    Marque ``est_actuelle=1`` uniquement sur les carrières du mois actif SME.

    Au plus **une** ligne par agent (``id`` max) pour éviter les doublons en liste.

    Si aucune ligne ``carriere_situation`` n'existe encore pour ``mois_actif`` (mois
    actif changé — ex. session SME au démarrage — vers une période sans import ni
    clôture/carry-forward), ne touche à rien : basculer le pointeur « mois actif »
    sur une période vide ne doit pas effacer la dernière situation connue de tout
    le monde. Sans cette garde, l'``UPDATE ... SET est_actuelle = 0`` inconditionnel
    ci-dessous videait ``est_actuelle`` pour tous les agents sans rien pour le
    remplacer — plus aucune statistique nulle part (tableau de bord, liste, etc.)
    tant qu'on ne revenait pas sur un mois qui a réellement des données.
    """
    actif_ym = canonical_sme_mois_yyyy_mm(mois_actif or "") if mois_actif else None
    with conn.cursor() as cur:
        if not actif_ym:
            cur.execute("UPDATE carriere_situation SET est_actuelle = 0 WHERE est_actuelle = 1")
            return 0
        cur.execute(
            """
            SELECT 1 FROM carriere_situation c
            INNER JOIN sme_periode p ON p.id = c.sme_periode_id
            WHERE p.annee_mois = %s LIMIT 1
            """,
            (actif_ym,),
        )
        if cur.fetchone() is None:
            return 0
        cur.execute("UPDATE carriere_situation SET est_actuelle = 0 WHERE est_actuelle = 1")
        cur.execute(
            """
            UPDATE carriere_situation c
            INNER JOIN (
                SELECT c2.agent_id, MAX(c2.id) AS keep_id
                FROM carriere_situation c2
                INNER JOIN sme_periode p ON p.id = c2.sme_periode_id AND p.annee_mois = %s
                WHERE c2.date_fin IS NULL
                   OR c2.date_fin >= LAST_DAY(DATE(CONCAT(p.annee_mois, '-01')))
                GROUP BY c2.agent_id
            ) best ON best.keep_id = c.id
            SET c.est_actuelle = 1
            """,
            (actif_ym,),
        )
        return int(cur.rowcount or 0)


def segment_dates_for_sme_mois(ym: str, *, open_segment: bool) -> tuple[date, date | None]:
    """
    Bornes du segment carrière pour un mois SME.

    Convention : ``date_fin`` = dernier jour inclus du mois ; NULL si segment ouvert.
    """
    debut = first_day_of_month(ym)
    fin = None if open_segment else last_day_of_month(ym)
    return debut, fin


def refresh_carriere_segment_dates(conn: Any) -> int:
    """
    Aligne ``date_debut`` / ``date_fin`` depuis ``sme_periode`` pour tous les segments.

    Segment ``est_actuelle=1`` : ``date_fin`` NULL ; historique : fin = dernier jour du mois.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE carriere_situation c
            INNER JOIN sme_periode p ON p.id = c.sme_periode_id
            SET c.date_debut = DATE(CONCAT(p.annee_mois, '-01')),
                c.date_fin = CASE
                    WHEN c.est_actuelle = 1 THEN NULL
                    ELSE LAST_DAY(DATE(CONCAT(p.annee_mois, '-01')))
                END
            """
        )
        return int(cur.rowcount or 0)


def is_historical_import(mois_import: str, mois_actif: str | None) -> bool:
    """Vrai si le mois importé est antérieur au mois de travail actif."""
    imp = normalize_import_mois(mois_import)
    act = canonical_sme_mois_yyyy_mm(mois_actif or "")
    if not act:
        return False
    return imp < act


def purge_sme_periode_for_reimport(settings: Settings, mois: str, *, imported_only: bool = True,
                                 preserve_manual_passations: bool = False,
                                 _replace_position_links: bool = False) -> dict:
    """Retire une période, en préservant par défaut les saisies manuelles.

    Les événements durables attestés par d'autres situations sont conservés.
    Le vidage complet utilise explicitement imported_only=False.
    """
    from sme_gend.services.operation_progress import operation_progress
    with operation_progress((('periods', 'Identification des périodes du mois'),
                             ('tables', 'Suppression des données par table'),
                             ('cache', 'Actualisation du cache mensuel'))) as progress:
        return _purge_sme_periode_for_reimport(settings, mois, imported_only=imported_only,
            preserve_manual_passations=preserve_manual_passations,
            _replace_position_links=_replace_position_links, progress=progress)


def _purge_sme_periode_for_reimport(settings: Settings, mois: str, *, imported_only,
                                   preserve_manual_passations, _replace_position_links, progress):
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.month_purge_plan import month_purge_plan, preserve_supported_events
    if _replace_position_links:
        from sme_gend.repositories.mysql_connection import has_import_transaction
        if not has_import_transaction(settings):
            raise RuntimeError('Remplacement des liens de position sans transaction de restauration.')
    ym = normalize_import_mois(mois)
    from sme_gend.services.month_period_scope import month_period_ids
    counts = {"sme_mois": ym, "agent_orphans_removed": 0}
    progress.start('periods', detail=f'Identification et verrouillage du mois {ym}…')
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            ids = month_period_ids(cur, ym, lock=True)
            if not ids:
                progress.finish('periods', detail=f'Aucune période rattachée au mois {ym}.')
                progress.finish('tables', detail='Aucune donnée mensuelle à supprimer.')
                progress.finish('cache', detail='Aucune modification du cache nécessaire.')
                return dict(counts, skipped=1)
            counts["sme_periode_id"] = ids[0]
            counts["sme_periode_ids"] = list(ids)
            plans = []
            for pid in ids:
                plan = month_purge_plan(pid, ym, imported_only=imported_only, target_period_ids=ids)
                if preserve_manual_passations:
                    from dataclasses import replace
                    plan = [replace(item, predicate=item.predicate + " AND COALESCE(d.obs,'') LIKE 'Import archive %%'")
                            if item.table == 'agent_passation_grade' else item for item in plan]
                plans.append((pid, plan))
            progress.finish('periods', detail=f'{len(ids)} période(s) rattachée(s) au mois {ym}.')
            total = sum(len(plan) + 2 for _, plan in plans)
            completed = 0
            progress.start('tables', total=total, detail='Contrôle des dépendances et suppression des tables mensuelles…')
            for period_number, (pid, plan) in enumerate(plans, 1):
                # This cache belongs to the selected snapshot; actual dated
                # movements remain durable global facts across imports/purges.
                cur.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                            "AND TABLE_NAME='sme_month_membership'")
                if cur.fetchone():
                    cur.execute('DELETE FROM sme_month_membership WHERE sme_periode_id=%s',(pid,))
                completed += 1
                progress.update('tables', current=completed, total=total,
                                detail=f'Période {period_number}/{len(ids)} : projection mensuelle traitée.')
                if not _replace_position_links:
                    from sme_gend.services.automatic_position_links import external_position_links
                    if external_position_links(cur, plan, ym):
                        raise ValueError('Un autre mois dépend d’une activité de ce mois. '
                                         'Le vidage / réimport est refusé pour préserver ses positions et retours validés.')
                preserve_supported_events(cur, plan, pid, ym)
                completed += 1
                progress.update('tables', current=completed, total=total,
                                detail=f'Période {period_number}/{len(ids)} : événements partagés et dépendances contrôlés.')
                for item in plan:
                    progress.update('tables', current=completed, total=total,
                        detail=f'Période {period_number}/{len(ids)} — {item.table} : suppression en cours…')
                    cur.execute(f"DELETE d FROM {item.table} d WHERE {item.predicate}", item.params)
                    removed = int(cur.rowcount or 0)
                    counts[item.table] = counts.get(item.table, 0) + removed
                    completed += 1
                    progress.update('tables', current=completed, total=total,
                        detail=f'Période {period_number}/{len(ids)} — {item.table} : {removed} ligne(s) supprimée(s).')
            progress.finish('tables', detail=f'{sum(counts.get(item.table, 0) for item in plans[0][1])} ligne(s) mensuelle(s) supprimée(s).')
    progress.start('cache', detail='Invalidation du cache mensuel…')
    invalidate_liste_cache(None)
    progress.finish('cache', detail='Cache mensuel invalidé ; attente de validation par l’opération appelante.')
    return counts


def prepare_import_context(
    settings: Settings,
    mois: str,
    access_path: Path | None,
) -> tuple[int, int, str, bool]:
    """
    Crée période + lot, retourne (periode_id, lot_id, mois_normalisé, is_historical).
    """
    from sme_gend.config.sme_context import load_sme_context

    ensure_mysql_migrations(settings)
    ym = normalize_import_mois(mois)
    ctx = load_sme_context()
    historical = is_historical_import(ym, ctx.mois_actif)
    mc = MySQLConnection(settings)
    with mc.connect() as conn:
        pid = ensure_sme_periode(conn, ym)
        lid = create_import_lot(conn, pid, access_path)
    return pid, lid, ym, historical
