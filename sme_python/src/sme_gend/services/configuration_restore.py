"""Configuration-only restore keeps live routing and records its recovery outcome."""
from contextlib import contextmanager, ExitStack
from io import BytesIO
from pathlib import Path
from threading import RLock
import json
import re
import unicodedata
import zipfile

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction, mysql_database_key
from sme_gend.services.restore_files import RestoreFiles, write_recovery, digest

_profile_lock = RLock()
_PROTECTED = ('shared_personnel_media','shared_data_revision','migration_v2_complete',
              'active_month_revision','sme_document_cycle')
_PREFIXES = ('audit_mirror:','configuration_restore:')


class ConfigurationRestoreRecoveryRequired(RuntimeError):
    committed = None


class ConfigurationRestoreCommittedError(RuntimeError):
    committed = True


class IncompatibleRestoreError(ValueError):
    """The archive cannot form a coherent database at the requested scope."""


def _fold(key):
    return ''.join(c for c in unicodedata.normalize('NFKD',key).casefold()
                   if unicodedata.category(c) not in ('Mn','Cf')).strip()


def operational_key(key):
    key = _fold(key)
    return key in _PROTECTED or key.startswith(_PREFIXES)


def _operational_predicate(column, collation):
    if not re.fullmatch(r'[A-Za-z0-9_]+', collation or ''):
        raise ValueError('Collation des clés de configuration non prise en charge.')
    predicates = [f'{column} COLLATE {collation} = %s' for _ in _PROTECTED]
    predicates += [f'LEFT({column},{len(prefix)}) COLLATE {collation} = %s' for prefix in _PREFIXES]
    return '(' + ' OR '.join(predicates) + ')', (*_PROTECTED, *_PREFIXES)


def apply_prepared_configuration(prepared, rows, local_auth, *, created_tables,
                                 preserve_audit, full_requested):
    """Keep live operational state unless the SQL actually restores the whole schema.

    This runs on the maintenance-owned staging connection. Filtering JSON alone
    cannot protect keys imported by the SQL dump, or preserve a live key's absence.
    """
    from types import SimpleNamespace
    from sme_gend.services.database_maintenance import check_owned_barrier
    from sme_gend.services.mysql_restore_staging import identifier
    from sme_gend.services.schema_migrations import LEDGER, PUBLICATION_TABLES, migrations
    check_owned_barrier(prepared.settings, prepared.conn)
    original = set(prepared.original)
    created_tables = set(created_tables)
    installed_extensions = {table for migration in migrations() for table in migration.tables} & original
    if full_requested and installed_extensions - created_tables:
        raise IncompatibleRestoreError('Sauvegarde complète incompatible avec les extensions installées : '
                         'elle ne contient pas toutes les données du nouveau système. '
                         'Restaurez cette ancienne sauvegarde dans une base séparée, ou sélectionnez '
                         'explicitement les référentiels à reprendre.')
    required = original - ({'audit_journal'} if preserve_audit else set())
    preserve_runtime = not required <= created_tables
    protocol_tables = {LEDGER, *PUBLICATION_TABLES, 'sme_temporal_change', 'sme_projection_dirty'}
    if preserve_runtime and created_tables & protocol_tables:
        raise IncompatibleRestoreError('Les registres centraux et les métadonnées système ne peuvent '
            'pas être restaurés séparément des données. Utilisez une sauvegarde complète cohérente ; '
            'les capacités et les données actuelles ont été conservées.')
    membership_tables = {'agent_effectif_movement', 'sme_month_membership'}
    cohort_sources = {'agent', 'sme_periode', 'carriere_situation', 'import_lot', 'mouvement_effectif'}
    if (preserve_runtime and original & membership_tables and created_tables & cohort_sources
            and not membership_tables <= created_tables):
        raise IncompatibleRestoreError('Une restauration partielle des effectifs ne peut pas conserver '
            'les mouvements datés et leur projection actuels. Utilisez la restauration mensuelle dédiée '
            'ou une sauvegarde complète cohérente.')
    if membership_tables & set(prepared.prepared):
        from sme_gend.services.schema_contracts import inspect_schema, errors
        shape_errors = errors(tuple(sorted(membership_tables)), inspect_schema(prepared.conn))
        if shape_errors:
            raise IncompatibleRestoreError('La famille des mouvements datés ne respecte pas son contrat : '
                                          + '; '.join(shape_errors[:10]))
        from dataclasses import replace
        from sme_gend.services.membership_projection import validate_membership_snapshot
        try:
            validate_membership_snapshot(replace(prepared.settings, mysql_database=prepared.new))
        except ValueError as exc:
            raise IncompatibleRestoreError('Sauvegarde des effectifs incohérente ; aucune bascule effectuée. '
                                           + str(exc)) from exc
    if preserve_runtime and rows:
        rows = _filter_database_keys(SimpleNamespace(connection=prepared.conn), rows)
    prepared.apply_config(rows, local_auth)
    if not preserve_runtime or 'app_config' not in prepared.prepared:
        return preserve_runtime
    with prepared.conn.cursor() as cur:
        live = identifier(prepared.live)
        new = identifier(prepared.new)
        schemas = (prepared.new, prepared.live) if 'app_config' in original else (prepared.new,)
        predicates = []
        values = []
        for schema in schemas:
            cur.execute('SELECT COLLATION_NAME,CHARACTER_SET_NAME FROM information_schema.COLUMNS '
                        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='app_config' AND COLUMN_NAME='config_key'", (schema,))
            row = cur.fetchone()
            if not row or not re.fullmatch(r'[A-Za-z0-9_]+', row[1] or ''):
                raise ValueError('Définition de clé de configuration incompatible avec une restauration partielle.')
            collation, charset = row
            condition, args = _operational_predicate(f'CONVERT(config_key USING {charset})', collation)
            predicates.append(condition)
            values.extend(args)
        cur.execute(f'DELETE FROM {new}.app_config WHERE ' + ' OR '.join(predicates), values)
        if 'app_config' in original:
            # The last predicate is evaluated using the original database's key
            # collation. Preserve the exact key spelling, payload and metadata.
            cur.execute(f'INSERT INTO {new}.app_config(config_key,payload,updated_by,updated_at) '
                        f'SELECT config_key,payload,updated_by,updated_at FROM {live}.app_config WHERE '
                        + predicates[-1], (*_PROTECTED, *_PREFIXES))
    return preserve_runtime


@contextmanager
def _sql_gate(settings):
    from sme_gend.services.media_storage_coordination import media_storage_gate
    from sme_gend.services.database_maintenance import centralized_maintenance
    # The maintenance owner holds the exclusive barrier on its own session.
    # Acquiring that same lock from the media session would block ourselves.
    with centralized_maintenance(settings, reason='Restauration des configurations partagées'):
        with media_storage_gate(settings) as gate:
            with gate.connection.cursor() as cur:
                cur.execute("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_config'")
                row = cur.fetchone()
                if not row or str(row[0]).upper() != 'INNODB':
                    raise RuntimeError('La restauration de configuration exige app_config en InnoDB.')
                cur.execute('SET SESSION innodb_lock_wait_timeout=5')
            yield gate


def _filter_database_keys(gate, rows, *, preserve_accounts=False):
    # Match protected names using the actual key collation, including aliases
    # that Python casefold alone would miss (for example Turkish dotless i).
    with gate.connection.cursor() as cur:
        cur.execute("SELECT COLLATION_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='app_config' AND COLUMN_NAME='config_key'")
        collation = cur.fetchone()[0]
        if not re.fullmatch(r'[A-Za-z0-9_]+',collation or ''):
            raise ValueError('Collation des clés de configuration non prise en charge.')
        protected_keys = _PROTECTED + (('auth_config',) if preserve_accounts else ())
        predicates = [f'%s COLLATE {collation} = %s' for _ in protected_keys]
        predicates += [f'LEFT(%s,{len(prefix)}) COLLATE {collation} = %s' for prefix in _PREFIXES]
        kept = []
        for row in rows:
            args = []
            for protected in (*protected_keys,*_PREFIXES):args.extend((row['config_key'],protected))
            cur.execute('SELECT '+ ' OR '.join(predicates),args)
            if cur.fetchone()[0]:continue
            if kept:
                cur.execute(f'SELECT %s COLLATE {collation} IN ('+', '.join(['%s']*len(kept))+')',
                            [row['config_key']]+[previous['config_key'] for previous in kept])
                if cur.fetchone()[0]:
                    raise ValueError('Clés de configuration dupliquées selon la collation MySQL ; restauration refusée.')
            kept.append(row)
    return kept


def _before_rows(settings,rows):
    if not rows:return []
    keys = sorted(row['config_key'] for row in rows)
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cur:
            cur.execute('SELECT config_key,payload FROM app_config WHERE config_key IN ('+
                        ','.join(['%s']*len(keys))+') ORDER BY config_key FOR UPDATE',keys)
            return [{'config_key':key,'payload':json.loads(raw) if isinstance(raw,str) else raw}
                    for key,raw in cur.fetchall()]


def _record(files,state,phase):
    state['phase'] = phase
    write_recovery(files.workspace/'configuration_recovery.json',json.dumps(state,ensure_ascii=False).encode('utf-8'))


def _committed(gate,state):
    gate.check()
    # A current locking read waits for an in-flight INSERT/COMMIT/ROLLBACK.
    # A plain snapshot read could mistake a still-uncommitted marker for absence.
    gate.connection.begin()
    try:
        with gate.connection.cursor() as cur:
            cur.execute('SELECT payload FROM app_config WHERE config_key=%s FOR UPDATE',(state['marker'],))
            row = cur.fetchone()
    finally:
        gate.connection.rollback()  # Read-only probe; advisory locks stay held.
    if not row:return False
    raw = json.loads(row[0]) if isinstance(row[0],str) else row[0]
    if raw != {'operation':state['operation'],'state':'committed'}:
        raise ValueError('Marqueur de récupération de configuration invalide.')
    return True


def _finish_or_rollback(files,state,gate):
    if state['sql']:
        committed = _committed(gate,state)
        if not committed and files.phase == 'completed':
            raise RuntimeError('Une restauration terminée ne possède plus son marqueur ; vérification manuelle requise.')
    else:
        committed = files.phase == 'completed'
    if committed:
        if files.phase not in ('published','completed'):
            raise RuntimeError('Publication des fichiers incohérente avec la validation SQL.')
        files.complete();_record(files,state,'completed')
    else:
        files.rollback();_record(files,state,'rolled_back')
    return committed


def _apply(settings,files,state,rows,repository,gate,operation=None):
    if operation is not None:
        operation.start('safety', detail='Conservation des fichiers originaux et des paramètres partagés')
    _record(files,state,'prepared')
    try:
        if gate is not None:
            from sme_gend.services.data_sync import publish_data_change
            with mysql_import_transaction(settings,read_committed=True):
                repository.set_json(state['marker'],{'operation':state['operation'],'state':'committed'})
                before = _before_rows(settings,rows)
                write_recovery(files.workspace/'configuration_before.json',json.dumps(before,ensure_ascii=False).encode('utf-8'))
                if operation is not None:
                    operation.finish('safety', detail=f'{len(before)} paramètre(s) et copies des fichiers conservés')
                    operation.start('restore', total=len(rows), detail='Restauration des paramètres partagés')
                restored = repository.import_config_rows(rows)
                if operation is not None:
                    operation.update('restore', current=restored, total=len(rows))
                    operation.finish('restore')
                    operation.start('verify', detail='Contrôle du nombre de paramètres et du verrou de maintenance')
                if restored != len(rows):raise RuntimeError('Restauration de configuration incomplète.')
                publish_data_change(settings,reason='configuration_restore')
                from sme_gend.services.auth_context import resolve_audit_actor
                repository.append_audit(action='configuration_restored', module='configuration',
                    actor=resolve_audit_actor(), payload={'detail':'Configuration partagée restaurée', 'recovery_session':state['operation'], 'config_count':len(rows)})
                gate.check();_record(files,state,'publishing')
                if operation is not None:
                    operation.finish('verify')
                    operation.start('publish', detail='Publication des fichiers et validation de la transaction')
                files.publish()
                _record(files,state,'committing');gate.check()
            _record(files,state,'committed')
        else:
            if operation is not None:
                operation.finish('safety', detail='Copies de récupération des fichiers conservées')
                operation.start('restore')
                operation.finish('restore', detail='Aucun paramètre SQL sélectionné')
                operation.start('verify')
                operation.finish('verify', detail='Fichiers préparés et empreintes de récupération vérifiés')
                operation.start('publish', detail='Publication des fichiers')
            _record(files,state,'publishing');files.publish()
        files.complete();_record(files,state,'completed')
    except BaseException as exc:
        try:
            if state['phase']=='prepared':
                files.rollback();_record(files,state,'rolled_back');committed=False
            else:
                committed = _finish_or_rollback(files,state,gate)
        except Exception as recovery_error:
            _record(files,state,'recovery_required')
            raise ConfigurationRestoreRecoveryRequired(
                f'Restauration à vérifier ; ne la relancez pas. Copies conservées : {files.workspace}'
            ) from recovery_error
        if committed:
            raise ConfigurationRestoreCommittedError(
                'Configuration déjà enregistrée malgré une erreur de confirmation. '
                'Ne recommencez pas la restauration ; actualisez les postes.'
            ) from exc
        raise


def _target(backup,name,kind,media_root):
    from sme_gend.services.safe_paths import relative_parts,contained_path
    parts = relative_parts(name)
    if kind=='media' and parts[0]==backup.PERSONNEL_MEDIA_DIR and len(parts)>=3:
        return contained_path(media_root,'/'.join(parts[1:]))
    if kind=='config' and parts[0]==backup.CONFIG_DIR and len(parts)==2 and parts[1] not in backup._SKIP_CONFIG_FILES:
        return backup._config_restore_target(parts[1])
    if kind=='data' and parts[0]==backup.DATA_DIR and len(parts)==2:
        return contained_path(backup.project_root()/'data',parts[1])
    if kind=='publication':
        from sme_gend.services.sme_publication_backup import restore_target
        return restore_target(name)
    raise ValueError('Plan de récupération de configuration invalide.')


def _load_files(backup,workspace,state,media_root):
    if not (workspace/'files').resolve().is_relative_to(workspace.resolve()):
        raise ValueError('Copies de récupération en dehors de la session privée.')
    raw = json.loads((workspace/'files_recovery.json').read_text(encoding='utf-8'))
    entries = raw.get('entries');plan = state.get('plan')
    if not isinstance(entries,list) or not isinstance(plan,list) or len(entries)!=len(plan):
        raise ValueError('Journal de fichiers incomplet.')
    targets=set()
    for number,(entry,item) in enumerate(zip(entries,plan)):
        expected = _target(backup,item['name'],item['kind'],media_root).resolve()
        if Path(entry['target']).resolve()!=expected or str(expected).casefold() in targets:
            raise ValueError('Destination de récupération déplacée ou invalide ; examen manuel requis.')
        targets.add(str(expected).casefold())
        for field,suffix in (('staged','new'),('original','old')):
            if Path(entry[field]).resolve()!=(workspace/'files'/f'{number}.{suffix}').resolve():
                raise ValueError('Copie de récupération hors de la session.')
        for field in ('before','after'):
            value=entry[field]
            if (field=='after' or value is not None) and not re.fullmatch('[0-9a-f]{64}',value or ''):
                raise ValueError('Empreinte de récupération invalide.')
        if digest(Path(entry['staged']))!=entry['after']:raise ValueError('Fichier préparé altéré.')
        if any(type(entry.get(flag)) is not bool for flag in ('publishing','published')):
            raise ValueError('État de récupération invalide.')
    files=RestoreFiles.__new__(RestoreFiles)
    files.workspace=workspace;files.entries=entries;files.targets=targets;files.phase=raw['phase']
    return files


def _recover_pending(backup,settings,base):
    from sme_gend.services.shared_media import shared_media_write_target
    for journal in sorted((base/'restore_sessions').glob('*/configuration_recovery.json')):
        state=json.loads(journal.read_text(encoding='utf-8'))
        if state.get('phase') in ('completed','rolled_back'):continue
        workspace=journal.parent
        if not workspace.resolve().is_relative_to((base/'restore_sessions').resolve()):
            raise ConfigurationRestoreRecoveryRequired('Session de récupération en dehors du dossier autorisé.')
        operation=workspace.name
        if (not re.fullmatch('[0-9a-f]{32}',operation) or state.get('operation')!=operation
                or state.get('version')!=1 or type(state.get('sql')) is not bool
                or state.get('marker')!='configuration_restore:'+operation
                or state.get('database')!=list(mysql_database_key(settings))):
            raise ConfigurationRestoreRecoveryRequired(f'Session de restauration à vérifier : {workspace}')
        try:
            with ExitStack() as stack:
                maintenance = None
                if state['sql']:
                    from sme_gend.services.database_maintenance import centralized_maintenance
                    maintenance = stack.enter_context(centralized_maintenance(settings,
                        reason='Reprise des configurations partagées', recovery_token=state.get('maintenance_token')))
                gate=stack.enter_context(_sql_gate(settings)) if state['sql'] else None
                root=None
                if any(item['kind']=='media' for item in state['plan']):
                    root=backup.resolved_personnel_media_root()
                    root=stack.enter_context(shared_media_write_target(root,settings=settings,gate=gate)).root
                files=_load_files(backup,workspace,state,root)
                try:
                    if state['phase']=='prepared':
                        if any(entry['publishing'] for entry in files.entries):
                            raise ValueError('Journal préparé incompatible avec une publication commencée.')
                        files.rollback();_record(files,state,'rolled_back')
                    else:
                        _finish_or_rollback(files,state,gate)
                except BaseException:
                    if maintenance is not None:
                        maintenance.keep_blocked()
                    raise
        except Exception as exc:
            raise ConfigurationRestoreRecoveryRequired(
                f'Reprise de configuration impossible sans vérification ; copies conservées : {workspace}'
            ) from exc


def restore_configuration_components(settings,archive,manifest,scope,rows,plan,files,*,operation=None):
    if operation is None:
        from sme_gend.services.operation_progress import operation_progress
        with operation_progress((('files', 'Préparation des fichiers'),
                                 ('safety', 'Conservation des copies de sécurité'),
                                 ('restore', 'Restauration des paramètres'),
                                 ('verify', 'Contrôle de la configuration'),
                                 ('publish', 'Publication et validation'))) as progress:
            return _restore_configuration_with_progress(settings,archive,manifest,scope,rows,plan,files,progress)
    return _restore_configuration_with_progress(settings,archive,manifest,scope,rows,plan,files,operation)


def _restore_configuration_with_progress(settings,archive,manifest,scope,rows,plan,files,operation):
    from sme_gend.services import backup_restore_service as backup
    from sme_gend.services.local_audit_mirror import locked_mirror_stream
    from sme_gend.services.shared_media import shared_media_write_target,invalidate_shared_media_cache
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
    rows=[row for row in rows if not operational_key(row['config_key'])
          and not (scope.preserve_local_accounts and _fold(row['config_key'])=='auth_config')]
    plan=[item for item in plan if not (item[2]=='config' and Path(item[1]).name=='client_instance.json')]
    base=backup.default_backup_directory()
    completed=False
    operation.start('files', total=len(plan), detail='Vérification des restaurations antérieures et des destinations')
    with locked_mirror_stream(base/'.configuration-restore.lock',thread_lock=_profile_lock):
        _recover_pending(backup,settings,base)
        try:
            with ExitStack() as stack:
                media=any(kind=='media' for _,_,kind in plan)
                requested=backup.resolved_personnel_media_root() if media else None
                needs_sql=bool(rows) or (media and str(requested).startswith(('\\','//')))
                from sme_gend.services.database_maintenance import centralized_maintenance
                maintenance = stack.enter_context(centralized_maintenance(settings, reason='Restauration des configurations partagées')) if needs_sql else None
                gate=stack.enter_context(_sql_gate(settings)) if needs_sql else None
                if gate is not None:rows=_filter_database_keys(gate,rows,preserve_accounts=scope.preserve_local_accounts)
                root=stack.enter_context(shared_media_write_target(requested,settings=settings,gate=gate)).root if media else None
                with zipfile.ZipFile(archive,'r') as source:
                    for index, (name,_,kind) in enumerate(plan, 1):
                        target=_target(backup,name,kind,root)
                        if kind=='config' and target.name=='paths.json':
                            expected_before=digest(target)
                            content=backup._merged_paths_json(json.loads(source.read(name).decode('utf-8')),target,
                                                              preserve_all_local_paths=True)
                            files.add(target,BytesIO(content),expected_before=expected_before)
                        else:
                            with source.open(name) as content:
                                if kind=='publication':
                                    files.add(target,content,expected_before=None)
                                else:
                                    files.add(target,content)
                        operation.update('files', current=index, total=len(plan), detail=f'{index}/{len(plan)} fichier(s) préparé(s) ; {name}')
                operation.finish('files')
                state=dict(version=1,operation=files.workspace.name,sql=gate is not None,
                    marker='configuration_restore:'+files.workspace.name,database=list(mysql_database_key(settings)),
                    plan=[dict(name=name,kind=kind) for name,_,kind in plan])
                if maintenance is not None:
                    state['maintenance_token'] = maintenance.token
                try:
                    _apply(settings,files,state,rows,backup.AppConfigRepository(settings) if gate else None,gate,operation)
                except ConfigurationRestoreRecoveryRequired:
                    if maintenance is not None:
                        maintenance.keep_blocked()
                    raise
                completed=True
            invalidate_shared_media_cache();invalidate_liste_cache();invalidate_editing_state_cache()
        except Exception as exc:
            if completed:
                raise ConfigurationRestoreCommittedError('Restauration terminée ; erreur de fin de coordination. Ne la recommencez pas.') from exc
            raise
    operation.finish('publish', detail='Fichiers, paramètres et caches actualisés')
    return manifest
