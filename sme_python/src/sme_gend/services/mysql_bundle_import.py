"""Import Access → MySQL (historique par période, stages, référentiels)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Callable

from sme_gend.config.settings import Settings
from sme_gend.repositories.access_connection import AccessConnection
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.repositories.sme_access_repository import AccessSmeRepository
from sme_gend.services.audit_log import append_audit_event
from sme_gend.services.mysql_import_period import (
    ensure_sme_periode,
    is_historical_import,
    normalize_import_mois,
    prepare_import_context,
    refresh_est_actuelle_flags,
    refresh_carriere_segment_dates,
)
from sme_gend.services.mysql_access_numerique_import import (
    import_desertions_from_access,
    import_malades_from_access,
    import_missions_from_access,
    import_mouvements_from_access,
    import_stages_from_access,
)
from sme_gend.services.mysql_ref_etl import (
    RefEtlCache,
    import_referentials_from_access,
)
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm


def _norm_id(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _str_or_empty(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _upsert_tab_rows(
    scur: Any,
    table: str,
    columns: list[str],
    key_cols: list[str],
    rows: list[tuple[Any, ...]],
) -> int:
    """INSERT … ON DUPLICATE KEY UPDATE (sans DELETE global)."""
    if not rows:
        return 0
    cols = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    non_key = [c for c in columns if c not in key_cols]
    if non_key:
        upd = ", ".join(f"{c}=VALUES({c})" for c in non_key)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {upd}"
    else:
        sql = f"INSERT IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
    for r in rows:
        scur.execute(sql, r)
    return len(rows)


_PREVIEW_COUNT_TABLES: tuple[str, ...] = (
    "ListeGenerale",
    "TabMission",
    "TabMalade",
    "TabDeserteur",
    "TabGainPerte",
    "TabStageLocal",
    "TabStageEx",
    "TabStageInterne",
    "TabConge",
)


def preview_access_import(access_settings: Settings) -> dict[str, Any]:
    """
    Aperçu en lecture seule d'un fichier Access avant import : décompte de lignes
    par table et avertissements de schéma (tables/colonnes attendues absentes).

    N'écrit rien en MySQL — sert à afficher un aperçu avant confirmation.
    """
    from sme_gend.services.access_schema_probe import probe_access_schema

    ac = AccessConnection(access_settings)
    warnings = probe_access_schema(ac)

    counts: dict[str, int] = {}
    try:
        with ac.connect() as conn:
            cur = conn.cursor()
            for table in _PREVIEW_COUNT_TABLES:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM [{table}]")
                    row = cur.fetchone()
                    counts[table] = int(row[0]) if row else 0
                except Exception:
                    continue  # table absente/illisible : déjà signalé par probe_access_schema
    except Exception as e:
        warnings.append(f"Lecture des décomptes impossible : {e}")

    return {"counts": counts, "warnings": warnings}


def copy_referentials_and_satellites_to_mysql(
    access_settings: Settings,
    mysql_settings: Settings,
    *,
    mois: str,
    sme_periode_id: int,
    import_lot_id: int,
    sync_operational_tabs: bool,
    # True = effacer toute la table satellite avant import (mois actif).
    # False = effacer uniquement les lignes de sme_periode_id (import historique).
    include_referentials: bool = True,
    include_agent_satellites: bool = True,
    ref_cache: RefEtlCache | None = None,
) -> dict[str, Any]:
    from sme_gend.repositories.mysql_common import ensure_mysql_schema

    ensure_mysql_schema(mysql_settings)
    counts: dict[str, int] = {}
    ac = AccessConnection(access_settings)
    mc = MySQLConnection(mysql_settings)

    from sme_gend.services.access_schema_probe import probe_access_schema

    warnings: list[str] = probe_access_schema(ac) if include_referentials else []

    def pull(query: str) -> tuple[list[tuple[Any, ...]], str | None]:
        try:
            with ac.connect() as conn:
                cur = conn.cursor()
                cur.execute(query)
                return list(cur.fetchall()), None
        except Exception as e:
            return [], str(e)

    from contextlib import closing
    from sme_gend.services.access_identity_map import read_identity_map, map_person_query
    with closing(ac.connect()) as identity_connection:
        identity_map = read_identity_map(identity_connection)
    raw_pull = pull
    def pull(query):
        rows, error = raw_pull(query)
        return map_person_query(query, rows, identity_map), error

    from sme_gend.services.active_month_guard import period_state_write
    with period_state_write(mysql_settings,reason="career_import",mois=mois):
        cache = ref_cache
        with mc.connect() as conn:
            with conn.cursor() as scur:
                if include_referentials:
                    cache = import_referentials_from_access(scur, pull)
                    counts.update({f"ref_{k}": v for k, v in cache.counts.items()})

                    try:
                        sme_rows = AccessSmeRepository(access_settings).fetch_all()
                        ym = normalize_import_mois(mois)
                        codsme_hint = None
                        statut_hint = None
                        for sr in sme_rows:
                            if canonical_sme_mois_yyyy_mm(sr.mois or "") == ym:
                                codsme_hint = sr.codsme
                                statut_hint = sr.statut
                                break
                        from sme_gend.services.active_month_guard import preserved_import_period
                        preserved_import_period(conn,ym,codsme=codsme_hint,statut=statut_hint,selected_month=ym)
                        for sr in sme_rows:
                            ym_row = canonical_sme_mois_yyyy_mm(sr.mois or "")
                            if ym_row:
                                preserved_import_period(conn,ym_row,codsme=sr.codsme,statut=sr.statut,selected_month=ym)
                                scur.execute(
                                    "SELECT id FROM sme_periode WHERE annee_mois=%s", (ym_row,)
                                )
                                pr = scur.fetchone()
                                if pr:
                                    cache.periode_by_ym[ym_row] = int(pr[0])
                    except Exception as e:
                        raise RuntimeError("Import annulé : les périodes SME de l'archive n'ont pas pu être vérifiées.") from e

                if include_agent_satellites:
                    if cache is None:
                        cache = RefEtlCache()
                    _import_agent_satellites(
                        scur,
                        pull,
                        ref_cache=cache,
                        mois=mois,
                        sme_periode_id=sme_periode_id,
                        import_lot_id=import_lot_id,
                        sync_operational_tabs=sync_operational_tabs,
                        counts=counts,
                        warnings=warnings,
                    )

    out: dict[str, Any] = {"counts": counts, "warnings": warnings}
    if cache is not None:
        out["ref_cache"] = cache
    return out


def _import_agent_satellites(
    scur: Any,
    pull: Callable[[str], tuple[list[tuple[Any, ...]], str | None]],
    *,
    ref_cache: RefEtlCache,
    mois: str,
    sme_periode_id: int,
    import_lot_id: int,
    sync_operational_tabs: bool,
    counts: dict[str, int],
    warnings: list[str],
) -> None:
    """Missions / maladies / … — à appeler APRÈS la liste générale (agents présents)."""
    stage_counts = import_stages_from_access(
        scur,
        pull,
        mois=mois,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
    )
    counts.update(stage_counts)

    counts["agent_mission"] = import_missions_from_access(
        scur,
        pull,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        replace_all=sync_operational_tabs,
    )
    counts["agent_malade"] = import_malades_from_access(
        scur,
        pull,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        replace_all=sync_operational_tabs,
    )
    counts["agent_desertion"] = import_desertions_from_access(
        scur,
        pull,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        replace_all=sync_operational_tabs,
    )

    mouvement_diagnostics: dict[str, int] = {}
    counts["mouvement_effectif"] = import_mouvements_from_access(
        scur,
        pull,
        ref_cache,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        mois_import=mois,
        replace_all=sync_operational_tabs,
        diagnostics=mouvement_diagnostics,
    )
    counts["mouvement_effectif_ignored_other_month"] = mouvement_diagnostics.get("other_month", 0)
    ignored_other_month = mouvement_diagnostics.get("other_month", 0)
    if ignored_other_month:
        warnings.append(
            f"Gains & Pertes : {ignored_other_month} ligne(s) Access ignorée(s), "
            f"car leur Cdsme appartient à un autre mois que {mois}."
        )

    from sme_gend.services.agent_conge_service import import_conges_from_access

    n_conge, conge_note = import_conges_from_access(
        scur,
        pull,
        sme_periode_id=sme_periode_id,
        import_lot_id=import_lot_id,
        replace_all=sync_operational_tabs,
    )
    counts["agent_conge_access"] = n_conge
    if conge_note:
        warnings.append(conge_note)


def import_full_mysql_bundle_from_access(
    access_settings: Settings,
    mysql_settings: Settings | None = None,
    *,
    progress: Callable[[str], None] | None = None,
    mois: str | None = None,
    on_month_committed=None,
    preserve_manual: bool = True,
    replacement_preview=None,
) -> dict[str, Any]:
    if not mois:
        raise ValueError("Le mois de l'import (YYYY-MM) est obligatoire.")

    mysql_st = mysql_settings or access_settings
    if not preserve_manual:
        from sme_gend.services.import_replacement import authorize_replacement
        authorize_replacement(False)
        if replacement_preview is None:
            raise ValueError("Un aperçu confirmé est requis pour remplacer entièrement le mois.")

    from sme_gend.services.backup_restore_service import ensure_recent_backup

    try:
        if progress:
            progress("Sauvegarde de sécurité…")
        backup_path = ensure_recent_backup(mysql_st, max_age_days=0.0)
        if backup_path is None:
            raise RuntimeError("Aucune sauvegarde de sécurité n'a été créée.")
    except Exception as exc:
        raise RuntimeError(
            "L'import est annulé car la sauvegarde de sécurité préalable a échoué. "
            "Vérifiez l'espace disque et les droits d'accès au dossier des sauvegardes."
        ) from exc

    original_access_path = access_settings.database_path
    from sme_gend.services.access_odbc import cleanup_staged_copy, stage_access_file_copy

    if progress:
        progress("Copie du fichier Access…")
    staged_path: Path | None = None
    if original_access_path.is_file():
        try:
            staged_path = stage_access_file_copy(original_access_path)
            access_settings = dataclasses.replace(access_settings, database_path=staged_path)
        except OSError:
            staged_path = None  # copie impossible (disque plein, permissions…) : lecture directe
    try:
        from sme_gend.repositories.mysql_common import ensure_mysql_schema
        from sme_gend.repositories.mysql_connection import mysql_import_transaction
        from sme_gend.services.app_config_store import ensure_central_config_ready

        # DDL et migration de configuration avant toute suppression : MySQL
        # valide implicitement les transactions lors d'un CREATE/ALTER TABLE.
        ensure_mysql_schema(mysql_st)
        ensure_central_config_ready(mysql_st)
        from sme_gend.services.active_month_guard import period_state_write
        with period_state_write(mysql_st,reason="career_import",mois=mois) as change:
            if replacement_preview is not None:
                from sme_gend.services.import_replacement import check_import_replacement
                check_import_replacement(mysql_st, replacement_preview, mois=mois, preserve_manual=preserve_manual)
            if on_month_committed is not None:
                from sme_gend.repositories.mysql_connection import after_mysql_commit
                after_mysql_commit(lambda: on_month_committed(change.result))
            return _run_mysql_bundle_import(
                access_settings,
                mysql_st,
                original_access_path=original_access_path,
                progress=progress,
                mois=mois,
                **({"preserve_manual": False} if not preserve_manual else {}),
            )
    finally:
        cleanup_staged_copy(staged_path)
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache
        from sme_gend.padmin_present import invalidate_padmin_presence_cache

        # Après commit ou rollback, aucune vue ne doit conserver des lectures
        # intermédiaires effectuées pendant le remplacement.
        invalidate_liste_cache(mysql_st)
        invalidate_padmin_presence_cache(mysql_st)


def _run_mysql_bundle_import(
    access_settings: Settings,
    mysql_st: Settings,
    *,
    original_access_path: Path,
    progress: Callable[[str], None] | None,
    mois: str,
    preserve_manual: bool = True,
) -> dict[str, Any]:
    from sme_gend.config.sme_context import load_sme_context
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    from sme_gend.services.liste_generale_import import import_liste_generale_access_to_mysql
    from sme_gend.services.import_stability import ensure_import_schema

    if progress:
        progress("Préparation du contexte d'import…")
    ensure_import_schema(mysql_st)
    ym = normalize_import_mois(mois)
    from sme_gend.services.active_month_guard import require_period_scope
    active = require_period_scope(mysql_st).before.active_month
    historical = is_historical_import(ym, active)
    # Satellites : toujours importés ; effacement global seulement pour le mois actif.
    replace_all_satellites = not historical

    periode_id, lot_id, ym, _ = prepare_import_context(
        mysql_st,
        ym,
        original_access_path,
    )

    if progress:
        progress(f"Remplacement des données et historiques du mois {ym}…")
    replacement_counts = replace_import_month_data(access_settings, mysql_st, ym,
        **({"preserve_manual": False} if not preserve_manual else {}))

    if progress:
        progress(f"Référentiels ({ym})…")
    ref = copy_referentials_and_satellites_to_mysql(
        access_settings,
        mysql_st,
        mois=ym,
        sme_periode_id=periode_id,
        import_lot_id=lot_id,
        sync_operational_tabs=replace_all_satellites,
        include_referentials=True,
        include_agent_satellites=False,
    )
    if progress:
        progress(f"Liste générale — historique {ym}…")
    ref_cache = ref.get("ref_cache")
    if not isinstance(ref_cache, RefEtlCache):
        ref_cache = None
    ins, upd, imported_ids, lg_stats = import_liste_generale_access_to_mysql(
        access_settings,
        mysql_st,
        record_audit=False,
        mois=ym,
        sme_periode_id=periode_id,
        import_lot_id=lot_id,
        historical=historical,
        ref_cache=ref_cache,
        detail_progress=getattr(progress, "detail", None),
    )
    if progress:
        progress("Satellites (missions, maladies, stages…)…")
    sat = copy_referentials_and_satellites_to_mysql(
        access_settings,
        mysql_st,
        mois=ym,
        sme_periode_id=periode_id,
        import_lot_id=lot_id,
        sync_operational_tabs=replace_all_satellites,
        include_referentials=False,
        include_agent_satellites=True,
        ref_cache=ref_cache,
    )
    ref_counts = ref.setdefault("counts", {})
    if isinstance(ref_counts, dict):
        sat_counts = sat.get("counts") or {}
        if isinstance(sat_counts, dict):
            ref_counts.update(sat_counts)
    sat_warn = sat.get("warnings") or []
    if isinstance(sat_warn, list) and sat_warn:
        ref.setdefault("warnings", []).extend(sat_warn)

    if progress:
        progress("Liaison des satellites (missions, maladies…)…")
    repo = MySQLAgentRepository(mysql_st)
    repo.link_satellite_agent_ids()

    if progress:
        progress("Enrichissement des dossiers agents…")
    from sme_gend.services.agent_dossier_import_enrichment import enrich_dossier_after_import

    dossier_enrich = enrich_dossier_after_import(
        mysql_st,
        ym,
        imported_ids,
        import_lot_id=lot_id,
    )

    if progress:
        progress("Synchronisation des congés…")
    from sme_gend.services.agent_conge_service import sync_conges_from_carriere_period

    conge_padmin = 0
    mc = MySQLConnection(mysql_st)
    with mc.connect() as conn:
        with conn.cursor() as cur:
            conge_padmin = sync_conges_from_carriere_period(
                cur,
                sme_periode_id=periode_id,
                import_lot_id=lot_id,
            )
    if progress:
        progress("Réparation des doublons carrière…")
    from sme_gend.services.mysql_import_period import repair_carriere_liste_duplicates

    repair_carriere_liste_duplicates(mysql_st)

    if progress:
        progress(f"Réconciliation référentiels carrière ({ym})…")
    from sme_gend.services.carriere_reconciliation import reconcile_after_import

    carriere_reconcile = reconcile_after_import(
        mysql_st,
        mois=ym,
        import_lot_id=lot_id,
    )

    if progress:
        progress("Projection de la carrière (dossiers)…")
    from sme_gend.services.sot_maintenance import project_carriere_from_dossier_batch

    dossier_projection = project_carriere_from_dossier_batch(
        mysql_st,
        mois=ym,
        only_est_actuelle=not historical,
    )

    if progress:
        progress("Alignement période active (est_actuelle)…")
    mc = MySQLConnection(mysql_st)
    with mc.connect() as conn:
        from sme_gend.services.sme_workspace_service import SmeWorkspaceService

        # Mois importé non historique = nouvelle situation de référence,
        # même si le mois_actif session n'a pas encore basculé.
        target_ym = ym if not historical else SmeWorkspaceService(mysql_st).get_active_mois()
        n_flag = refresh_est_actuelle_flags(conn, target_ym)
        if n_flag == 0 and not historical:
            refresh_est_actuelle_flags(conn, SmeWorkspaceService(mysql_st).get_active_mois())
        refresh_carriere_segment_dates(conn)

    if progress:
        progress("Finalisation…")
    from sme_gend.services.shared_reference_write import mark_shared_data_write
    mark_shared_data_write(True)

    append_audit_event(
        action="mysql_bundle_import_access",
        access_database=str(original_access_path.resolve()),
        mysql_database=mysql_st.mysql_database,
        sme_mois_context=ym,
        sme_mois=ym,
        import_historical=historical,
        liste_inserted=ins,
        liste_updated=upd,
        month_replacement=replacement_counts,
        preserve_manual=preserve_manual,
        liste_generale_stats=lg_stats.to_dict(),
        dossier_enrichment=dossier_enrich,
        conge_padmin_sync=conge_padmin,
        carriere_reconciliation=carriere_reconcile.to_summary_dict(),
        dossier_projection=dossier_projection,
        ref_counts=ref.get("counts"),
        ref_warnings=ref.get("warnings"),
    )
    return {
        "mysql_database": mysql_st.mysql_database,
        "access_database": str(original_access_path.resolve()),
        "sme_mois": ym,
        "sme_periode_id": periode_id,
        "import_lot_id": lot_id,
        "historical": historical,
        "referentials": ref,
        "liste_inserted": ins,
        "liste_updated": upd,
        "month_replacement": replacement_counts,
        "preserve_manual": preserve_manual,
        "liste_generale_stats": lg_stats.to_dict(),
        "dossier_enrichment": dossier_enrich,
        "conge_padmin_sync": conge_padmin,
        "carriere_reconciliation": carriere_reconcile,
        "dossier_projection": dossier_projection,
    }


def replace_import_month_data(access_settings: Settings, mysql_settings: Settings, mois: str, *, preserve_manual: bool = True) -> dict:
    """Vérifie la source avant d'effacer les données du seul mois remplacé."""
    from sme_gend.repositories.liste_generale_access_repository import AccessListeGeneraleRepository
    from sme_gend.services.mysql_import_period import purge_sme_periode_for_reimport

    # fetch_all doit réussir même pour une source vide ; une erreur de lecture
    # ou de schéma ne doit jamais déclencher l'effacement du mois existant.
    AccessListeGeneraleRepository(access_settings).fetch_all()
    if preserve_manual:
        return purge_sme_periode_for_reimport(mysql_settings, mois)
    from sme_gend.services.import_replacement import authorize_replacement, EXTRA_TABLES
    from sme_gend.repositories.mysql_connection import MySQLConnection
    authorize_replacement(False)
    from sme_gend.repositories.mysql_connection import has_import_transaction
    if not has_import_transaction(mysql_settings):
        raise RuntimeError("Le remplacement complet doit être effectué dans la transaction d’import.")
    counts = purge_sme_periode_for_reimport(mysql_settings, mois, imported_only=False)
    with MySQLConnection(mysql_settings).connect() as conn:
        with conn.cursor() as cur:
            for table in EXTRA_TABLES:
                cur.execute(f"DELETE FROM {table} WHERE mois=%s", (normalize_import_mois(mois),))
                counts[table] = int(cur.rowcount or 0)
    return counts
