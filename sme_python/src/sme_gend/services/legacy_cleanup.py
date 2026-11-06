"""Explicit DDL maintenance phase: verified backup, journal and honest outcomes."""
from dataclasses import dataclass,field
from pathlib import Path
from uuid import uuid4
import hashlib,json,re
import pymysql
from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_database_key,has_import_transaction
from sme_gend.repositories.mysql_pool import invalidate_mysql_pool
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.automatic_maintenance import identifier,assert_permission
from sme_gend.services.mysql_restore_staging import maintenance_lock_name
from sme_gend.services.safe_paths import atomic_destination
from sme_gend.services.restore_files import write_recovery,digest as file_digest
from sme_gend.services.mysql_private_options import _restrict_folder
from sme_gend.services.mysql_backup_service import _python_sql_dump,_literal

LEGACY=('tab_gain_perte','tab_mission','tab_malade','tab_deserteur','tab_stage_local','tab_stage_ex',
    'tab_sectrois','tab_secdeux','tab_secun','tab_padmin','tab_grade','tab_structure','tab_sme')


@dataclass(frozen=True,slots=True)
class LegacyPlan:
    database:tuple=field(repr=False)
    state:tuple=field(repr=False)
    @property
    def token(self):return fingerprint(('legacy_drop',self.database,self.state))
    def summary(self):return dict(mode='apercu',operation='legacy_drop',revision=self.token,tables={r[0]:r[2] for r in self.state},writes_applied=False)


class LegacyCleanupError(RuntimeError):
    def __init__(self,message,report):super().__init__(message);self.report=report


def _insert(table,columns,row):
    return 'INSERT INTO '+identifier(table)+' ('+','.join(identifier(c) for c in columns)+') VALUES ('+','.join(_literal(v) for v in row)+');'


def _inventory(conn):
    state=[]
    with conn.cursor() as cur:
        cur.execute('SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
        rows=cur.fetchall();selected=sorted(str(name) for name,_,_ in rows if str(name).lower() in LEGACY)
        if not any(name=='app_config' for name,_,_ in rows):raise RuntimeError('Initialisez le schema applicatif avant la suppression legacy.')
        if any((str(engine).upper(),kind)!=('INNODB','BASE TABLE') for name,engine,kind in rows if name in selected):
            raise RuntimeError('Suppression legacy prise en charge uniquement pour des tables InnoDB.')
        if not selected:return ()
        placeholders=','.join(['%s']*len(selected))
        cur.execute('SELECT TABLE_SCHEMA,TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IN ('+placeholders+') AND (TABLE_SCHEMA<>DATABASE() OR TABLE_NAME NOT IN ('+placeholders+'))',tuple(selected+selected))
        if cur.fetchall():raise RuntimeError('Des tables actives referencent les tables legacy : suppression refusee.')
        cur.execute('SELECT VIEW_SCHEMA,VIEW_NAME FROM information_schema.VIEW_TABLE_USAGE WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('+placeholders+')',tuple(selected))
        if cur.fetchall():raise RuntimeError('Des vues dependent des tables legacy : suppression refusee.')
        for table in selected:
            cur.execute('SHOW CREATE TABLE '+identifier(table));ddl=cur.fetchone()[1]
            cur.execute('SELECT * FROM '+identifier(table));columns=[field[0] for field in cur.description]
            hashes=[]
            while True:
                batch=cur.fetchmany(500)
                if not batch:break
                hashes.extend(hashlib.sha256(_insert(table,columns,row).encode('utf-8')).hexdigest() for row in batch)
            state.append((table,hashlib.sha256(ddl.encode('utf-8')).hexdigest(),len(hashes),fingerprint(sorted(hashes))))
    return tuple(state)


def prepare_legacy_cleanup(settings):
    if has_import_transaction(settings):raise RuntimeError('Preparez la suppression legacy hors transaction metier.')
    with MySQLConnection(settings).connect(read_only=True) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            return LegacyPlan(mysql_database_key(settings),_inventory(conn))
        finally:conn.rollback()


def _verify_backup(path,state,counts):
    hashes={row[0]:[] for row in state};ddl={};current=None;lines=[]
    with path.open(encoding='utf-8') as file:
        for line in file:
            match=re.match(r'CREATE TABLE `([^`]+)`',line)
            if match:current=match[1];lines=[]
            if current:
                lines.append(line)
                if line.rstrip().endswith(';'):
                    ddl[current]=hashlib.sha256(''.join(lines).rstrip('\n').removesuffix(';').encode('utf-8')).hexdigest();current=None
            if line.startswith('INSERT INTO '):
                match=re.match(r'INSERT INTO `([^`]+)`',line)
                if match is None or match[1] not in hashes:raise RuntimeError('Table inattendue dans la sauvegarde legacy.')
                hashes[match[1]].append(hashlib.sha256(line.rstrip('\n').encode('utf-8')).hexdigest())
    actual=tuple((table,ddl.get(table),len(hashes[table]),fingerprint(sorted(hashes[table]))) for table in sorted(hashes))
    if actual!=state or counts!={row[0]:row[2] for row in state}:raise RuntimeError('La sauvegarde ne correspond pas au plan ; aucune table supprimee.')


def _execute_drop(cur,names):cur.execute('DROP TABLE '+','.join(identifier(name) for name in names))


def _other_clients(cur):
    from sme_gend.services.database_maintenance import _owner
    owner = _owner.get()
    cur.execute('SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE DB=DATABASE() AND ID<>CONNECTION_ID() AND ID<>%s',
                (owner.connection_id if owner is not None else -1,))
    if cur.fetchone()[0]:raise RuntimeError('Fermez les autres postes et connexions sur cette base avant la suppression legacy.')


def run_legacy_cleanup(settings,*,backup_dir,maintenance_confirmed=False,expected=None):
    assert_permission('legacy_drop')
    if has_import_transaction(settings):raise RuntimeError('DDL interdit dans une transaction metier.')
    if not maintenance_confirmed:raise ValueError('Confirmez la maintenance et la fermeture des autres postes avant la suppression.')
    if expected is None:raise ValueError('Un plan de suppression legacy verifie est obligatoire.')
    if isinstance(expected,LegacyPlan):
        if expected.database!=mysql_database_key(settings):raise ValueError('Plan legacy pour une autre base.')
        expected=expected.token
    if not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None:raise ValueError('Revision legacy invalide.')
    if not backup_dir:raise ValueError('Dossier de sauvegarde obligatoire.')
    invalidate_mysql_pool(settings)
    params=MySQLConnection(settings).connect_params();params.update(autocommit=True,read_timeout=300,write_timeout=300)
    conn=pymysql.connect(**params);lock=maintenance_lock_name(settings);locked=False;table_lock=False;original_fk=None
    report={'phase':'preparation','tables_dropped':[],'tables_remaining':None,'outcome_known':True,'notification_published':False,'notification_outcome_known':True}
    journal=None;primary_error=None
    from sme_gend.services.database_maintenance import centralized_maintenance
    write_gate=centralized_maintenance(settings, reason='Nettoyage des anciennes tables');gate_entered=False
    def record(phase):
        report['phase']=phase
        if journal is not None:write_recovery(journal,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
    try:
        maintenance = write_gate.__enter__();gate_entered=True
        with conn.cursor() as cur:
            cur.execute('SELECT VERSION()');version=str(cur.fetchone()[0])
            if not re.match(r'^(8|9)\.',version) or 'mariadb' in version.lower():raise RuntimeError('La suppression securisee exige MySQL 8 ou 9 compatible InnoDB.')
            _other_clients(cur)
            state=_inventory(conn)
            if LegacyPlan(mysql_database_key(settings),state).token!=expected:raise ReferenceConflictError('Les tables legacy ont change depuis le plan. Regenerez le plan avant toute suppression.')
            names=[row[0] for row in state]
            if not names:return report|dict(phase='aucune_table',tables_remaining=[],writes_applied=False)
        folder=Path(backup_dir).resolve()/('legacy_cleanup_'+uuid4().hex);folder.mkdir(parents=True,exist_ok=False)
        try:_restrict_folder(folder)
        except OSError as error:raise RuntimeError('Impossible de proteger le dossier de sauvegarde ; aucune suppression appliquee.') from error
        backup=folder/'before_drop.sql';journal=folder/'recovery.json'
        report.update(backup=str(backup),journal=str(journal),planned_tables=names,revision=expected)
        record('backup_started')
        with atomic_destination(backup,temporary_suffix='.sql',replace_existing=False) as staged:
            counts=_python_sql_dump(settings,staged,selected_tables=names)
            _verify_backup(staged,state,counts)
        report['backup_sha256']=file_digest(backup);record('backup_verified')
        invalidate_mysql_pool(settings)
        with conn.cursor() as cur:
            _other_clients(cur)
            cur.execute('SET SESSION lock_wait_timeout=15')
            cur.execute('LOCK TABLES '+','.join(identifier(name)+' WRITE' for name in names+['app_config']));table_lock=True
            if _inventory(conn)!=state:raise ReferenceConflictError('Les tables legacy ont change pendant la sauvegarde ; aucune suppression appliquee.')
            cur.execute('SELECT @@SESSION.FOREIGN_KEY_CHECKS');original_fk=int(cur.fetchone()[0])
            record('drop_started');cur.execute('SET FOREIGN_KEY_CHECKS=0')
            _execute_drop(cur,names)
            cur.execute('SET FOREIGN_KEY_CHECKS='+str(original_fk));original_fk=None
            cur.execute('UNLOCK TABLES');table_lock=False
        report.update(tables_dropped=names,tables_remaining=[]);record('dropped_notification_pending')
        report['notification_outcome_known']=False
        from sme_gend.services.shared_reference_write import shared_reference_write,mark_reference_write
        with shared_reference_write(settings):mark_reference_write(True)
        report.update(notification_published=True,notification_outcome_known=True,writes_applied=True);record('complete')
        return report
    except BaseException as error:
        primary_error=error
        if journal is not None:
            # Never claim rollback for DDL. Inspect actual tables even if a
            # driver/connection lost the acknowledgement of the DROP.
            try:
                with conn.cursor() as cur:
                    if table_lock:cur.execute('UNLOCK TABLES');table_lock=False
                    cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
                    present={row[0] for row in cur.fetchall()}
                report['tables_remaining']=[name for name in report['planned_tables'] if name in present]
                report['tables_dropped']=[name for name in report['planned_tables'] if name not in present]
            except Exception:report['outcome_known']=False
            if report.get('tables_dropped') or not report['outcome_known']:
                # DDL cannot roll back. Keep admission closed until the
                # operator has checked the recovery report and actual tables.
                maintenance.keep_blocked()
            report['interrupted_during']=report['phase']
            try:record('interrupted')
            except Exception:report['journal_write_failed']=True
            raise LegacyCleanupError('Suppression legacy interrompue : gardez la maintenance, consultez le journal et la sauvegarde ; ne relancez pas sans verifier le resultat.',report.copy()) from error
        raise
    finally:
        failures=[]
        commands=[]
        if original_fk is not None:commands.append(('SET FOREIGN_KEY_CHECKS='+str(original_fk),()))
        if table_lock:commands.append(('UNLOCK TABLES',()))
        if locked:commands.append(('SELECT RELEASE_LOCK(%s)',(lock,)))
        for command,arguments in commands:
            try:
                with conn.cursor() as cur:cur.execute(command,arguments)
            except Exception as cleanup_error:failures.append(cleanup_error)
        try:
            import sys
            if gate_entered:write_gate.__exit__(*sys.exc_info())
        except Exception as cleanup_error:failures.append(cleanup_error)
        try:conn.close()
        except Exception as cleanup_error:failures.append(cleanup_error)
        if failures:
            if primary_error is not None:primary_error.add_note('Nettoyage de connexion incertain ; maintenir la maintenance.')
            else:
                try:record('cleanup_interrupted')
                except Exception:report['journal_write_failed']=True
                raise LegacyCleanupError('Tables traitees mais nettoyage de connexion incertain ; verifier le journal et maintenir la maintenance.',report.copy()) from failures[0]
