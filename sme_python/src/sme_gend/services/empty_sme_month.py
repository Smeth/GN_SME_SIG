"""Vidage d'un mois : données métier, historique et copies d'archives."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from sme_gend.config.settings import Settings
from sme_gend.services.mysql_import_period import normalize_import_mois, purge_sme_periode_for_reimport


def month_archive_files(root: Path, mois: str) -> list[Path]:
    from sme_gend.services.sme_month_closure import archive_slug_candidates_for_month

    root = root.resolve()
    files = set()
    for slug in archive_slug_candidates_for_month(mois):
        for prefix, suffixes in (
            ("sme_bundle_", (".mdb", ".accdb")),
            ("liste_present_layout_", (".json",)),
        ):
            for path in root.glob(f"{prefix}{slug}_*"):
                # Aucun parcours de sous-dossier ni lien vers un fichier externe.
                if path.is_file() and not path.is_symlink() and path.suffix.lower() in suffixes:
                    resolved = path.resolve()
                    if resolved.parent == root:
                        files.add(resolved)
    return sorted(files)


def empty_sme_month(settings: Settings, mois: str, *, imported_only: bool = False) -> dict:
    from sme_gend.services.auth_guard import session_has_admin_role
    from sme_gend.repositories.mysql_common import ensure_mysql_schema
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction, CommittedMirrorError
    from sme_gend.services.app_config_store import ensure_central_config_ready
    from sme_gend.services.backup_restore_service import ensure_recent_backup
    from sme_gend.services.mysql_import_period import ensure_sme_periode
    from sme_gend.services.sme_month_closure import sme_archives_dir
    from sme_gend.services.audit_log import append_audit_event
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache

    if not session_has_admin_role():
        raise PermissionError("Le vidage complet d'un mois est réservé aux administrateurs.")
    ym = normalize_import_mois(mois)
    ensure_mysql_schema(settings)
    ensure_central_config_ready(settings)
    backup = ensure_recent_backup(settings, max_age_days=0.0)
    if backup is None:
        raise RuntimeError("Vidage annulé : la sauvegarde préalable a échoué.")
    backup = Path(backup)
    root = sme_archives_dir().resolve()
    files = month_archive_files(root, ym)
    archive_backup = None
    if files:
        archive_backup = backup.with_name(f"{backup.stem}_archives_{ym}.zip")
        # Les copies Access ne sont pas incluses dans la sauvegarde MySQL standard.
        with ZipFile(archive_backup, "w", ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, arcname=path.name)

    warnings = []
    try:
        with mysql_import_transaction(settings):
            from sme_gend.services.month_period_scope import month_period_ids
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    ids = month_period_ids(cur, ym, lock=True)
                if not ids:
                    ids = (ensure_sme_periode(conn, ym),)
            counts = purge_sme_periode_for_reimport(settings, ym, imported_only=imported_only)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    for pid in ids:
                        for table in ("agent_diplome", "agent_distinction", "agent_affectation",
                                      "agent_mission", "agent_malade", "agent_desertion",
                                      "parcours_stage", "mouvement_effectif", "agent_conge",
                                      "carriere_situation"):
                            cur.execute(f"UPDATE {table} SET import_lot_id=NULL WHERE import_lot_id IN "
                                        "(SELECT id FROM import_lot WHERE sme_periode_id=%s)", (pid,))
                        cur.execute("DELETE e FROM etl_erreur e JOIN import_lot l ON l.id=e.import_lot_id WHERE l.sme_periode_id=%s", (pid,))
                        counts["etl_erreur"] = counts.get("etl_erreur", 0) + int(cur.rowcount or 0)
                        cur.execute("DELETE FROM import_lot WHERE sme_periode_id=%s", (pid,))
                        counts["import_lot"] = counts.get("import_lot", 0) + int(cur.rowcount or 0)
                    for table in (() if imported_only else ("workflow_task", "career_alert_ack", "career_alert_ack_occurrence")):
                        cur.execute(f"DELETE FROM {table} WHERE TRIM(REPLACE(mois, '-', ''))=%s", (ym.replace('-', ''),))
                        counts[table] = int(cur.rowcount or 0)
                    remaining = {}
                    placeholders = ','.join(['%s'] * len(ids))
                    for table in ("carriere_situation", "agent_mission", "agent_malade", "agent_desertion", "parcours_stage", "mouvement_effectif", "agent_conge"):
                        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE sme_periode_id IN ({placeholders})", ids)
                        remaining[table] = int(cur.fetchone()[0])
                    counts['remaining_month_rows'] = remaining
                    if not imported_only and any(remaining.values()):
                        raise RuntimeError("Vidage annulé : des données restent rattachées au mois. Aucune suppression validée.")
            from sme_gend.services.data_sync import publish_data_change
            publish_data_change(settings, reason="month_emptied", mois=ym)
            append_audit_event(
                action="sme_month_emptied", sme_mois_context=ym,
                result={**counts, "imported_only": imported_only, "backup": str(backup),
                        "archive_backup": str(archive_backup) if archive_backup else None,
                        "archives_pending": [p.name for p in files]},
            )
    except CommittedMirrorError:
        warnings.append("Mois vidé et trace centrale enregistrée ; la copie locale du journal n’a pas pu être écrite.")
    finally:
        invalidate_liste_cache(None)

    removed_files = 0
    for path in files:
        try:
            if path.resolve().parent != root or path.is_symlink():
                raise ValueError("Chemin d'archive modifié ; suppression refusée.")
            path.unlink()
            removed_files += 1
        except (OSError, ValueError) as exc:
            warnings.append(f"Archive conservée : {path.name} ({exc})")
    counts.update(imported_only=imported_only, archives_removed=removed_files, backup=str(backup),
                  archive_backup=str(archive_backup) if archive_backup else None,
                  warnings=warnings)
    if files:
        try:
            append_audit_event(action="sme_month_archives_cleaned", sme_mois_context=ym, result=counts)
        except Exception:
            warnings.append("Mois vidé et sauvegardé ; le résultat du nettoyage des archives n’a pas pu être journalisé. Consultez la sauvegarde des archives avant toute nouvelle intervention.")
    return counts


def preview_empty_sme_month(settings: Settings, mois: str, *, imported_only: bool = False) -> dict:
    """Lecture seule des mêmes critères que la suppression (estimation avant confirmation)."""
    from sme_gend.services.auth_guard import session_has_admin_role
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.services.month_purge_plan import month_purge_plan
    from sme_gend.services.sme_month_closure import sme_archives_dir
    if not session_has_admin_role():
        raise PermissionError("Réservé aux administrateurs.")
    ym = normalize_import_mois(mois)
    from sme_gend.services.month_period_scope import month_period_ids
    counts = {}
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            ids = month_period_ids(cur, ym)
            grouped = {}
            for pid in ids:
                for item in month_purge_plan(pid, ym, imported_only=imported_only, target_period_ids=ids):
                    grouped.setdefault(item.table, []).append(item)
                for table in ("import_lot", "etl_erreur"):
                    sql = ("SELECT COUNT(*) FROM import_lot WHERE sme_periode_id=%s" if table == "import_lot" else
                           "SELECT COUNT(*) FROM etl_erreur e JOIN import_lot l ON l.id=e.import_lot_id WHERE l.sme_periode_id=%s")
                    cur.execute(sql, (pid,)); counts[table] = counts.get(table, 0) + int(cur.fetchone()[0])
            for table, items in grouped.items():
                predicate = ' OR '.join(f'({item.predicate})' for item in items)
                params = tuple(value for item in items for value in item.params)
                cur.execute(f"SELECT COUNT(*) FROM {table} d WHERE {predicate}", params)
                counts[table] = int(cur.fetchone()[0])
            if not imported_only:
                for table in ("workflow_task", "career_alert_ack", "career_alert_ack_occurrence"):
                    cur.execute(f"SELECT COUNT(*) FROM {table} WHERE TRIM(REPLACE(mois, '-', ''))=%s", (ym.replace('-', ''),))
                    counts[table] = int(cur.fetchone()[0])
    counts["archives"] = len(month_archive_files(sme_archives_dir(), ym))
    return counts
