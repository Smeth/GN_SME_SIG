"""Explicit, backed-up reset of the configured application's shared database."""
from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import logging
from pathlib import Path
import traceback

from sme_gend.services.operation_progress import operation_progress

_log = logging.getLogger(__name__)
_RESET_DELETE_BATCH_SIZE = 1000
_RESET_PROGRESS_STEPS = (
    ('prepare', 'Préparation et verrouillage de la base'),
    ('backup', 'Sauvegarde de sécurité MySQL'),
    ('local_backup', 'Sauvegarde des fichiers locaux'),
    ('delete', 'Suppression des données'),
    ('check', 'Contrôle des tables vidées'),
    ('files', 'Retrait des fichiers locaux'),
    ('commit', 'Enregistrement et validation MySQL'),
    ('finalize', 'Finalisation et libération des verrous'),
)


@contextmanager
def _reset_step(label, *, backup=None):
    try:
        yield
    except Exception as exc:
        if not getattr(exc, 'reset_stage', None):
            exc.reset_stage = label
            if backup is not None:
                exc.reset_safety_backup = str(backup)
            code = exc.args[0] if exc.args and type(exc.args[0]) is int else None
            # Stack frames identify the failure without SQL parameters or payloads.
            _log.error('Réinitialisation interrompue : étape=%s ; type=%s ; code=%s\n%s',
                       label, type(exc).__name__, code, ''.join(traceback.format_tb(exc.__traceback__)))
        raise


def _delete_reset_table(cursor, table, *, keep_document_history, on_batch=None):
    quoted = '`' + table.replace('`', '``') + '`'
    where = " WHERE module IS NULL OR module <> 'doc_gen'" if table == 'audit_journal' and keep_document_history else ''
    total = 0
    while True:
        cursor.execute(f'DELETE FROM {quoted}{where} LIMIT {_RESET_DELETE_BATCH_SIZE}')
        removed = int(cursor.rowcount)
        total += removed
        if on_batch is not None:
            on_batch(total)
        if removed < _RESET_DELETE_BATCH_SIZE:
            return total


@contextmanager
def _reset_maintenance(settings, warnings, confirmed_result):
    from sme_gend.services.database_maintenance import centralized_maintenance
    try:
        with centralized_maintenance(settings, reason="Réinitialisation complete de l'application") as lease:
            yield lease
    except Exception:
        if not confirmed_result:
            raise
        warnings.append('Réinitialisation confirmée dans MySQL ; fermeture de la maintenance à vérifier. '
                        'Ne recommencez pas la réinitialisation.')
        _log.warning('Réinitialisation confirmée ; nettoyage de maintenance à contrôler.')


@dataclass(frozen=True)
class ResetOptions:
    keep_users: bool = True
    keep_document_history: bool = True


def reset_target(settings):
    return f"{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}"


def replacement_auth(username, password):
    from sme_gend.config.auth_config import AuthConfig, AuthUser
    from sme_gend.config.auth_permissions import ROLE_SUPER_ADMIN
    from sme_gend.services.auth_service import hash_new_password, validate_password
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("Renseignez le nouvel identifiant administrateur (64 caractères maximum).")
    hashed, salt = hash_new_password(validate_password(password))
    cfg = AuthConfig.from_dict({"enabled": True})
    cfg.users = [AuthUser(username=username, password_hash=hashed, salt=salt,
                          role=ROLE_SUPER_ADMIN, created_at=datetime.now(timezone.utc).isoformat())]
    return cfg.to_dict()


def reset_application(settings, options, *, confirmation, new_username="", new_password=""):
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    if not session_has_admin_role() or not session_has_permission("system.restore"):
        raise PermissionError("La réinitialisation est réservée aux administrateurs autorisés a restaurer.")
    if confirmation != reset_target(settings):
        raise ValueError("La confirmation ne correspond pas a la base cible.")
    auth = None if options.keep_users else replacement_auth(new_username, new_password)
    from sme_gend.repositories.mysql_connection import schema_metadata_lock
    result = None
    with operation_progress(_RESET_PROGRESS_STEPS) as progress:
        progress.start('prepare', detail='Acquisition du verrou de schéma…')
        try:
            with schema_metadata_lock(settings):
                result = _reset_application_locked(settings, options, auth=auth, progress=progress)
        except Exception:
            if result is None:
                raise
            result['warnings'].append('Réinitialisation confirmée ; libération du verrou de schéma à vérifier. '
                                      'Ne recommencez pas la réinitialisation.')
            _log.warning('Réinitialisation confirmée ; libération de schéma à contrôler.')
        progress.finish('finalize', detail='Réinitialisation terminée ; bilan prêt à consulter.')
    return result


def _reset_application_locked(settings, options, *, auth, progress):
    from sme_gend.services.app_config_store import ensure_central_config_ready
    from sme_gend.services.database_maintenance import INTERNAL_TABLES
    from sme_gend.services.backup_restore_service import create_backup_to_default_dir, inspect_backup
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction, CommittedMirrorError
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.data_sync import publish_data_change
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    ensure_central_config_ready(settings)
    warnings = []
    confirmed_result = {}
    with _reset_maintenance(settings, warnings, confirmed_result) as lease:
        progress.finish('prepare', detail='Maintenance acquise ; les écritures des autres postes sont suspendues.')
        # Admission is closed before the backup so no successful intervening write is lost.
        progress.start('backup', detail='Création et vérification de la sauvegarde de sécurité…')
        with _reset_step('sauvegarde de sécurité'):
            with progress.subtask('backup', current=0, total=2, detail='Création de la sauvegarde de sécurité…'):
                backup = Path(create_backup_to_default_dir(settings))
            with progress.subtask('backup', current=1, total=2, detail='Vérification de la sauvegarde de sécurité…'):
                inspect_backup(backup)  # Refuse deletion if the safety archive is invalid.
        progress.finish('backup', detail='Sauvegarde MySQL vérifiée.')
        from sme_gend.services.application_reset_files import ResetLocalFiles, reset_local_paths
        progress.start('local_backup', detail='Inventaire et copie de récupération des fichiers locaux…')
        files = ResetLocalFiles(backup.with_suffix('.reset-recovery'), reset_local_paths(settings,
                                keep_document_history=options.keep_document_history))
        progress.finish('local_backup', detail=f'{len(files.entries)} fichier(s) sauvegardé(s) et vérifié(s).')
        removed = {}
        commit_pending = False
        try:
            with _reset_step('validation de la transaction', backup=backup), mysql_import_transaction(settings):
                with MySQLConnection(settings).connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME")
                        tables = [r for r in cur.fetchall() if r[0] not in INTERNAL_TABLES]
                        # DDL capability state and the shared barrier survive a
                        # data reset. Kept documents retain their central bytes.
                        from sme_gend.services.schema_migrations import LEDGER, PUBLICATION_TABLES
                        preserved = {LEDGER, 'sme_publication_write_gate'}
                        if options.keep_document_history:
                            preserved.update(PUBLICATION_TABLES)
                        if any(r[1] != 'InnoDB' or r[2] != 'BASE TABLE' for r in tables):
                            raise RuntimeError("Réinitialisation annulée : objets non transactionnels dans la base.")
                        cur.execute("SELECT payload FROM app_config WHERE config_key='auth_config' FOR UPDATE")
                        row = cur.fetchone()
                        if options.keep_users and row is None:
                            raise RuntimeError("Comptes introuvables : aucune suppression effectuée.")
                        original_auth = json.loads(row[0]) if row and isinstance(row[0], str) else (row[0] if row else {})
                        if options.keep_users:
                            auth = original_auth
                        else:
                            auth['config_rev'] = int(original_auth.get('config_rev', 0)) + 1
                            auth['roles_rev'] = int(original_auth.get('roles_rev', 0)) + 1
                        delete_tables = [table for table, _, _ in tables if table not in preserved]
                        progress.start('delete', detail='Comptage des lignes à supprimer…')
                        expected_rows = {}
                        for table in delete_tables:
                            progress.update('delete', detail=f'Comptage des lignes à supprimer : {table}…')
                            quoted = '`' + table.replace('`', '``') + '`'
                            where = (" WHERE module IS NULL OR module <> 'doc_gen'"
                                     if table == 'audit_journal' and options.keep_document_history else '')
                            cur.execute(f'SELECT COUNT(*) FROM {quoted}{where}')
                            expected_rows[table] = int(cur.fetchone()[0])
                        total_rows = sum(expected_rows.values())
                        deleted_rows = 0
                        progress.update('delete', current=0, total=total_rows,
                                        detail=f'{total_rows} ligne(s) dans {len(delete_tables)} table(s).')
                        # DELETE is transactional; no TRUNCATE or implicit DDL commit.
                        cur.execute("SET SESSION FOREIGN_KEY_CHECKS=0")
                        body_failed = False
                        try:
                            for table_index, table in enumerate(delete_tables, 1):
                                with _reset_step('suppression de la table '+table, backup=backup):
                                    # Bounded statements avoid one huge DELETE exceeding
                                    # the client deadline. All batches share ONE transaction.
                                    base_rows = deleted_rows
                                    progress.update('delete', current=base_rows, total=total_rows,
                                        detail=f'Table {table_index}/{len(delete_tables)} : {table} — suppression en cours…')
                                    def on_batch(current, *, _base=base_rows, _table=table,
                                                 _index=table_index):
                                        progress.update('delete', current=_base + current, total=total_rows,
                                            detail=f'Table {_index}/{len(delete_tables)} : {_table} — '
                                                   f'{current}/{expected_rows[_table]} ligne(s) supprimée(s).')
                                    removed[table] = _delete_reset_table(cur, table,
                                        keep_document_history=options.keep_document_history, on_batch=on_batch)
                                    deleted_rows += removed[table]
                            progress.finish('delete', detail=f'{deleted_rows} ligne(s) supprimée(s) ; transaction non encore validée.')
                            # Every business table must really be empty before commit.
                            check_tables = [table for table in delete_tables if table not in ('app_config', 'audit_journal')]
                            progress.start('check', total=len(check_tables), detail='Vérification des tables métier…')
                            for check_index, table in enumerate(check_tables, 1):
                                progress.update('check', current=check_index - 1, total=len(check_tables),
                                                detail=f'Contrôle {check_index}/{len(check_tables)} : {table}…')
                                quoted = '`' + table.replace('`', '``') + '`'
                                cur.execute(f"SELECT COUNT(*) FROM {quoted}")
                                if cur.fetchone()[0]:
                                    raise RuntimeError("Réinitialisation incomplete : transaction annulée.")
                                progress.update('check', current=check_index, total=len(check_tables),
                                                detail=f'Table {check_index}/{len(check_tables)} vérifiée : {table}.')
                            progress.finish('check', detail='Toutes les tables métier sont vides dans la transaction.')
                        except BaseException:
                            body_failed = True
                            raise
                        finally:
                            try:
                                cur.execute("SET SESSION FOREIGN_KEY_CHECKS=1")
                            except BaseException:
                                getattr(conn, '_connection', conn)._sme_retire_after_write = True
                                if not body_failed:
                                    raise
                                _log.warning('Connexion de réinitialisation indisponible ; erreur initiale conservée.')
                progress.start('files', total=len(files.entries), detail='Retrait des fichiers sauvegardés…')
                files.remove()
                progress.finish('files', detail=f'{len(files.entries)} fichier(s) retiré(s) ; récupération disponible.')
                progress.start('commit', detail='Enregistrement de la configuration et du journal central…')
                repo = AppConfigRepository(settings)
                actor = resolve_audit_actor()
                repo.set_json('auth_config', auth, updated_by=actor)
                # Block legacy local JSON/JSONL reimports on every workstation.
                for key in ('liste_present_layout', 'detaches_config', 'limite_age_config',
                            'soit_transmis_config', 'ui_view_state', 'sme_workspace'):
                    repo.set_json(key, {}, updated_by=actor)
                repo.set_json('migration_v2_complete', {'reset': True}, updated_by=actor)
                from sme_gend.services.doc_gen_log import _publish_document_revision
                _publish_document_revision(settings)
                publish_data_change(settings, reason='application_reset')
                repo.append_audit(action='application_reset', module='system', actor=actor,
                    payload={'keep_users': options.keep_users, 'keep_document_history': options.keep_document_history,
                             'backup': str(backup), 'removed': removed})
                commit_pending = True
                progress.update('commit', detail='Validation de la transaction MySQL en cours…')
        except CommittedMirrorError:
            warnings.append("Base réinitialisée ; copie locale du journal indisponible. Ne recommencez pas.")
        except BaseException as exc:
            if commit_pending:
                lease.keep_blocked()
                # A lost COMMIT acknowledgement is not proof of rollback.
                # Keep the recovery files and maintenance for explicit checking.
                exc.reset_commit_uncertain = True
                exc.reset_safety_backup = str(backup)
                _log.error('Résultat de validation de réinitialisation incertain ; maintenance et récupération conservées.')
                raise
            try:
                files.rollback()
            except BaseException as recovery_error:
                lease.keep_blocked()
                recovery_error.reset_stage = 'récupération des fichiers'
                recovery_error.reset_safety_backup = str(backup)
                recovery_error.reset_recovery_required = True
                raise recovery_error from exc
            raise
        finally:
            invalidate_liste_cache(None)
        # This phase only finishes after the transaction context has acknowledged
        # COMMIT, or raised CommittedMirrorError with its explicit success contract.
        progress.finish('commit', detail='Réinitialisation confirmée dans MySQL.')
        progress.start('finalize', detail='Finalisation de la récupération des fichiers et fermeture de la maintenance…')
        try:
            files.committed()
        except OSError:
            warnings.append("Réinitialisation terminée ; consultez le dossier de récupération des fichiers.")
        confirmed_result.update(backup=str(backup), removed=removed, warnings=warnings,
                                recovery=str(files.workspace), local_files_removed=len(files.entries))
    return confirmed_result
