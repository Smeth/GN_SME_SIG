"""Select months from a full backup using an unpublished staging database."""
from dataclasses import dataclass, replace
from pathlib import Path
import shutil
from uuid import uuid4
from zipfile import ZipFile

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key, mysql_import_transaction
from sme_gend.services.operation_progress import operation_progress


@dataclass(frozen=True)
class BackupMonths:
    owner: tuple
    source_settings: object
    months: tuple[str, ...]
    workspace: Path


def _authorize():
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    if not session_has_admin_role() or not session_has_permission('system.restore'):
        raise PermissionError('La restauration de mois exige un administrateur autorisé à restaurer.')


def prepare_backup_months(settings, archive):
    with operation_progress((('read', 'Vérification de la sauvegarde'),
                             ('stage', 'Préparation des données de la sauvegarde'),
                             ('verify', 'Contrôle des données préparées'),
                             ('months', 'Lecture des mois disponibles'))) as operation:
        return _prepare_backup_months_with_progress(settings, archive, operation)


def _prepare_backup_months_with_progress(settings, archive, operation):
    operation.start('read')
    _authorize()
    from sme_gend.services.backup_restore_service import (
        default_backup_directory, inspect_backup, SQL_DUMP_NAME, _split_sql_statements,
        _should_run_sql_statement,
    )
    from sme_gend.services.mysql_restore_staging import prepared_restore
    from sme_gend.services.mysql_import_period import normalize_import_mois
    workspace = default_backup_directory() / 'restore_sessions' / uuid4().hex
    workspace.mkdir(parents=True)
    from sme_gend.services.mysql_private_options import _restrict_folder
    _restrict_folder(workspace)
    stable = workspace / 'source.zip'
    shutil.copyfile(archive, stable)
    manifest = inspect_backup(stable)
    if manifest.backup_kind != 'full':
        raise ValueError('Le choix de plusieurs mois exige une sauvegarde complète. '
                         'Pour cette archive partielle, utilisez la restauration par composants.')
    if not {'agent', 'sme_periode', 'carriere_situation'}.issubset(manifest.table_counts):
        raise ValueError('Cette sauvegarde ne contient pas les tables mensuelles nécessaires.')
    with ZipFile(stable) as source:
        if SQL_DUMP_NAME not in source.namelist():
            raise ValueError('La sauvegarde ne contient pas de données MySQL.')
        sql = source.read(SQL_DUMP_NAME).decode('utf-8')
    statements = [statement for statement in _split_sql_statements(sql)
                  if _should_run_sql_statement(statement, allowed_tables=None, preserve_audit_journal=False)]
    from sme_gend.services.backup_sql import target_table
    from sme_gend.services.database_maintenance import INTERNAL_TABLES
    created = {target_table(statement) for statement in statements
               if statement.lstrip().upper().startswith('CREATE')}
    if created != set(manifest.table_counts).difference(INTERNAL_TABLES):
        raise ValueError('Archive SQL incomplète : les tables ne correspondent pas au manifeste.')
    operation.finish('read', detail=f'{len(statements):,} instruction(s) vérifiée(s)')
    operation.start('stage')
    # No publish: the live tables, accounts and files are never replaced here.
    with prepared_restore(settings, statements, workspace, counts=manifest.table_counts) as staged:
        operation.finish('stage')
        operation.start('verify')
        staged.validate()
        operation.finish('verify')
        operation.start('months')
        source_settings = replace(settings, mysql_database=staged.new)
        with MySQLConnection(source_settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT DISTINCT annee_mois FROM sme_periode ORDER BY annee_mois')
                months = tuple(sorted({normalize_import_mois(str(row[0])) for row in cur.fetchall()}))
        from sme_gend.repositories.mysql_pool import invalidate_mysql_pool
        invalidate_mysql_pool(source_settings)
    if not months:
        raise ValueError('Aucun mois présent dans cette sauvegarde.')
    operation.finish('months', detail=f'{len(months)} mois disponible(s) : ' + ', '.join(months))
    return BackupMonths(mysql_database_key(settings), source_settings, months, workspace)


def restore_selected_backup_months(settings, prepared, selected):
    with operation_progress((('bundles', 'Préparation des mois sélectionnés'),
                             ('safety', 'Sauvegarde de sécurité de la base cible'),
                             ('restore', 'Restauration des mois sélectionnés'),
                             ('commit', 'Validation commune et finalisation'))) as operation:
        return _restore_selected_backup_months_with_progress(settings, prepared, selected, operation)


def _restore_selected_backup_months_with_progress(settings, prepared, selected, operation):
    _authorize()
    if prepared.owner != mysql_database_key(settings):
        raise ValueError('La base cible a changé ; choisissez à nouveau la sauvegarde.')
    selected = tuple(sorted(set(selected)))
    if not selected or not set(selected).issubset(prepared.months):
        raise ValueError('Sélectionnez uniquement des mois présents dans la sauvegarde.')
    from sme_gend.services.month_backup import _create_month_backup, restore_month_backup, inspect_month_backup
    from sme_gend.services.database_maintenance import centralized_maintenance
    from sme_gend.services.backup_restore_service import create_backup_to_default_dir, inspect_backup
    bundles = []
    operation.start('bundles', total=len(selected))
    for index, month in enumerate(selected):
        destination = prepared.workspace / ('month_' + month + '.zip')
        with operation.subtask('bundles', current=index, total=len(selected), detail=f'Mois {month}'):
            _create_month_backup(prepared.source_settings, month, destination)
            inspect_month_backup(destination)
        bundles.append((month, destination))
    operation.finish('bundles')
    with centralized_maintenance(settings, reason='Restauration des mois sélectionnés : ' + ', '.join(selected)):
        operation.start('safety')
        with operation.subtask('safety', current=0, total=2, detail='Création de la sauvegarde de sécurité'):
            safety = create_backup_to_default_dir(settings)
        with operation.subtask('safety', current=1, total=2, detail='Vérification de la sauvegarde de sécurité'):
            inspect_backup(safety)
        operation.finish('safety', detail=f'Sauvegarde vérifiée : {Path(safety).name}')
        operation.start('restore', total=len(bundles))
        # One shared transaction: failure of any selected month rolls back all.
        with mysql_import_transaction(settings):
            grade_corrections = 0
            for index, (month, archive) in enumerate(bundles):
                with operation.subtask('restore', current=index, total=len(bundles), detail=f'Mois {month}'):
                    result = restore_month_backup(settings, archive, confirmed_month=month, _safety_backup=safety)
                grade_corrections += result.get('grade_history_reapplied', 0)
            operation.finish('restore')
            operation.start('commit', detail='Validation unique de tous les mois ; attente de la confirmation MySQL')
    operation.finish('commit', detail=f'{len(selected)} mois restauré(s) dans une seule transaction')
    return {'months': selected, 'backup': str(safety), 'grade_history_reapplied': grade_corrections}
