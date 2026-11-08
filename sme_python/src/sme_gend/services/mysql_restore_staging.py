"""Prepare a complete InnoDB database, then exchange tables with one atomic DDL.

Requires MySQL 8 and a maintenance window. Old tables are retained, never dropped.
The caller keeps the recovery journal and controls publication of local files.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import re
from uuid import uuid4

import pymysql
from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
from sme_gend.services.backup_sql import target_table, validate_restore_statement, is_safe_setting
from sme_gend.services.restore_files import write_recovery
from sme_gend.services.operation_progress import operation_progress


def identifier(value):
    return '`' + str(value).replace('`','``') + '`'


def schema_names():
    prefix='sme_restore_' + uuid4().hex
    return prefix+'_new',prefix+'_old'


def maintenance_lock_name(settings):
    # GET_LOCK is already local to the MySQL server. Host aliases and ports
    # must not produce different locks for the same database on that server.
    from sme_gend.services.database_maintenance import lock_name
    return lock_name(settings)


def _create_body_end(statement):
    """Locate the structural closing parenthesis, excluding literals/identifiers."""
    quote=None;depth=0;end=None;i=statement.index('(')
    while i<len(statement):
        char=statement[i]
        if quote:
            if char=='\\' and quote!='`':i+=1
            elif char==quote:
                if i+1<len(statement) and statement[i+1]==quote:i+=1
                else:quote=None
        elif char in "'\"`":quote=char
        elif char=='(':depth+=1
        elif char==')':
            depth-=1
            if depth==0:end=i+1;break
        i+=1
    if end is None:raise ValueError('Définition de table incomplète.')
    return end


def validate_staged_statement(statement):
    validate_restore_statement(statement)
    if not statement.lstrip().upper().startswith('CREATE'):return
    end=_create_body_end(statement)
    suffix=statement[end:].strip().removesuffix(';').strip()
    # Only options emitted by supported SHOW CREATE TABLE are accepted.
    option=(r'(?:ENGINE\s*=\s*InnoDB|AUTO_INCREMENT\s*=\s*\d+|'
        r'DEFAULT\s+(?:CHARSET|CHARACTER\s+SET)\s*=\s*\w+|(?:DEFAULT\s+)?COLLATE\s*=\s*\w+|'
        r'ROW_FORMAT\s*=\s*(?:DYNAMIC|COMPACT|REDUNDANT|COMPRESSED)|KEY_BLOCK_SIZE\s*=\s*\d+|'
        r'STATS_PERSISTENT\s*=\s*[01]|COMMENT\s*=\s*\'(?:[^\'\\]|\\.|\'\')*\')')
    if not re.fullmatch(option+r'(?:\s+'+option+r')*',suffix,re.I) or not re.search(r'\bENGINE\s*=\s*InnoDB\b',suffix,re.I):
        raise ValueError('Options de table non prises en charge : restauration InnoDB requise.')
    # Foreign keys must stay within the prepared schema. Quoted literals are
    # removed so a comment/default containing REFERENCES does not trigger a rule.
    body=re.sub(r"'(?:[^'\\]|\\.|'')*'", "''",statement[:end])
    token=r'(?:`(?:``|[^`])+`|[A-Za-z_][A-Za-z0-9_$]*)'
    if re.search(r'\bREFERENCES\s+'+token+r'\s*\.',body,re.I):
        raise ValueError('Référence entre bases interdite dans la restauration.')


class PreparedRestore:
    def __init__(self,settings,workspace):
        self.settings=settings;self.workspace=workspace
        self.live=settings.mysql_database;self.new,self.old=schema_names()
        self.conn=None;self.created=[];self.original=[];self.prepared=[]
        self.published=False;self.uncertain=False;self.phase='created'
        self.lock=maintenance_lock_name(settings)

    def record(self,phase):
        self.phase=phase
        payload=dict(phase=phase,target=self.live,host=self.settings.mysql_host,
            port=self.settings.mysql_port,prepared_schema=self.new,previous_schema=self.old,
            original_tables=self.original,prepared_tables=self.prepared,
            published=self.published,uncertain=self.uncertain)
        write_recovery(self.workspace/'mysql_recovery.json',json.dumps(payload,indent=2).encode('utf-8'))

    def table_names(self,schema):
        with self.conn.cursor() as cur:
            cur.execute('SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME',(schema,))
            rows=cur.fetchall()
        if any(str(engine).upper()!='INNODB' or kind!='BASE TABLE' for _,engine,kind in rows):
            raise RuntimeError('La restauration sûre exige uniquement des tables InnoDB, sans vues.')
        from sme_gend.services.database_maintenance import INTERNAL_TABLES
        return [str(row[0]) for row in rows if str(row[0]) not in INTERNAL_TABLES]

    def prepare(self,statements,counts=None):
        with operation_progress((('validate', 'Vérification des instructions SQL'),
                                 ('clone', 'Préparation des tables conservées'),
                                 ('import', 'Restauration des instructions SQL'),
                                 ('verify', 'Contrôle des tables restaurées'))) as operation:
            return self._prepare_with_progress(statements, counts, operation)

    def _prepare_with_progress(self,statements,counts,operation):
        operation.start('validate', total=len(statements))
        from sme_gend.services.database_maintenance import _owner, INTERNAL_TABLES
        if _owner.get() is None:
            raise RuntimeError('La préparation exige une maintenance centralisée active.')
        if any(target_table(statement) in INTERNAL_TABLES for statement in statements):
            raise ValueError('L’état de maintenance ne peut pas être restauré depuis une archive.')
        for index, statement in enumerate(statements, 1):
            validate_staged_statement(statement)
            if index % 1000 == 0 or index == len(statements):
                operation.update('validate', current=index, total=len(statements),
                    detail=f'{index:,}/{len(statements):,} instruction(s) vérifiée(s)')
        creates=[target_table(s) for s in statements if s.lstrip().upper().startswith('CREATE')]
        drops=[target_table(s) for s in statements if s.lstrip().upper().startswith('DROP')]
        affected={target_table(s) for s in statements if not is_safe_setting(s)}
        if len(set(creates))!=len(creates) or set(drops)-set(creates) or affected-set(creates):
            raise ValueError('Le dump doit recréer chaque table remplacée, une seule fois.')
        if has_import_transaction(self.settings):raise RuntimeError('Restauration interdite dans une transaction métier.')
        operation.finish('validate')
        operation.start('clone', detail='Connexion à la base de préparation')
        params=MySQLConnection(self.settings).connect_params()
        params.update(autocommit=True,read_timeout=300,write_timeout=300)
        self.conn=pymysql.connect(**params)
        with self.conn.cursor() as cur:
            from sme_gend.services.database_maintenance import check_owned_barrier, worker_lock_name
            check_owned_barrier(self.settings, self.conn)
            cur.execute('SELECT GET_LOCK(%s,0)', (worker_lock_name(self.settings),))
            if cur.fetchone()[0] != 1:
                raise RuntimeError('Un autre traitement de restauration est encore actif.')
            cur.execute('SELECT VERSION()');version=str(cur.fetchone()[0])
            if 'mariadb' in version.casefold() or not re.match(r'^(?:8|9)\.',version):
                raise RuntimeError('La bascule atomique requiert MySQL 8 ou supérieur compatible InnoDB.')
            # The outer maintenance context holds the database-wide barrier.
            # This connection performs DDL but never owns/releases that barrier.
            cur.execute('SET SESSION lock_wait_timeout=15')
            cur.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_AUTO_VALUE_ON_ZERO'")
            for table,column in (('TRIGGERS','TRIGGER_SCHEMA'),('ROUTINES','ROUTINE_SCHEMA'),('EVENTS','EVENT_SCHEMA')):
                cur.execute(f'SELECT COUNT(*) FROM information_schema.{table} WHERE {column}=%s',(self.live,))
                if cur.fetchone()[0]:raise RuntimeError('Objets SQL avancés détectés : restauration spécialisée nécessaire avant toute bascule.')
            # Inbound and outbound cross-schema foreign keys cannot be moved safely.
            cur.execute('SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA IS NOT NULL AND '
                '((TABLE_SCHEMA=%s AND REFERENCED_TABLE_SCHEMA<>%s) OR (TABLE_SCHEMA<>%s AND REFERENCED_TABLE_SCHEMA=%s))',
                (self.live,self.live,self.live,self.live))
            if cur.fetchone()[0]:raise RuntimeError('Clés étrangères entre bases détectées : bascule refusée.')
            self.original=self.table_names(self.live)
            operation.update('clone', current=0, total=2 * len(self.original),
                detail=f'{len(self.original)} table(s) à préparer')
            for schema in (self.new,self.old):
                cur.execute(f'CREATE DATABASE {identifier(schema)} CHARACTER SET utf8mb4')
                self.created.append(schema)
            self.record('preparing')
            cur.execute(f'USE {identifier(self.new)}');cur.execute('SET FOREIGN_KEY_CHECKS=0')
            for index, table in enumerate(self.original, 1):
                cur.execute(f'SHOW CREATE TABLE {identifier(self.live)}.{identifier(table)}')
                ddl=cur.fetchone()[1];validate_staged_statement(ddl)
                cur.execute(ddl)
                operation.update('clone', current=index, total=2 * len(self.original), detail=f'Schéma : {table}')
            # Preserve every table not selected, including current-version tables
            # absent in an older archive. No selected live table is dropped.
            for index, table in enumerate(self.original, 1):
                operation.update('clone', current=len(self.original) + index - 1, total=2 * len(self.original),
                    detail=f'Copie des données : {table} ; attente du serveur')
                cur.execute('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s '
                    "AND EXTRA NOT LIKE '%%VIRTUAL GENERATED%%' AND EXTRA NOT LIKE '%%STORED GENERATED%%' ORDER BY ORDINAL_POSITION",(self.live,table))
                cols=','.join(identifier(row[0]) for row in cur.fetchall())
                cur.execute(f'INSERT INTO {identifier(table)} ({cols}) SELECT {cols} FROM {identifier(self.live)}.{identifier(table)}')
                operation.update('clone', current=len(self.original) + index, total=2 * len(self.original),
                    detail=f'Données copiées : {table}')
            operation.finish('clone')
            executable = [statement for statement in statements if not is_safe_setting(statement)]
            operation.start('import', total=len(executable))
            for index, statement in enumerate(executable, 1):
                cur.execute(statement)
                if index % 250 == 0 or index == len(executable):
                    operation.update('import', current=index, total=len(executable),
                        detail=f'{index:,}/{len(executable):,} instruction(s) appliquée(s) ; table {target_table(statement)}')
            operation.finish('import')
            operation.start('verify', total=len(set(creates)))
            self.prepared=self.table_names(self.new)
            if counts is not None:
                for index, table in enumerate(sorted(set(creates)), 1):
                    if table not in counts:raise ValueError('Table du dump absente du manifeste.')
                    expected=counts[table]
                    if isinstance(expected,bool) or not isinstance(expected,int) or expected<0:raise ValueError('Décompte de table invalide.')
                    cur.execute(f'SELECT COUNT(*) FROM {identifier(table)}')
                    if cur.fetchone()[0]!=expected:raise ValueError('Décompte de table différent du manifeste : archive refusée.')
                    operation.update('verify', current=index, total=len(set(creates)), detail=f'{table} : {expected:,} ligne(s) contrôlée(s)')
        self.record('prepared')
        operation.finish('verify', detail='Base de préparation prête ; base active inchangée')

    def apply_config(self,rows,local_auth):
        from sme_gend.services.database_maintenance import check_owned_barrier
        check_owned_barrier(self.settings, self.conn)
        if not rows and local_auth is None:return
        if 'app_config' not in self.prepared:raise RuntimeError('Table app_config absente : restauration de configuration impossible.')
        with self.conn.cursor() as cur:
            for row in rows:
                cur.execute('INSERT INTO app_config (config_key,payload,updated_by) VALUES (%s,%s,%s) '
                    'ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_by=VALUES(updated_by),updated_at=CURRENT_TIMESTAMP(3)',
                    (row['config_key'],json.dumps(row['payload'],ensure_ascii=False,allow_nan=False),'restore'))
            if local_auth is not None:
                cur.execute('INSERT INTO app_config (config_key,payload,updated_by) VALUES (%s,%s,%s) '
                    'ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_by=VALUES(updated_by),updated_at=CURRENT_TIMESTAMP(3)',
                    ('auth_config',json.dumps(local_auth,ensure_ascii=False,allow_nan=False),'restore_keep_accounts'))

    def record_restore_audit(self, *, archive_name, preserve_audit):
        """Prepare the restore trace in the same database as the atomic switch."""
        from sme_gend.services.auth_context import resolve_audit_actor
        from sme_gend.services.database_maintenance import check_owned_barrier
        check_owned_barrier(self.settings, self.conn)
        with self.conn.cursor() as cur:
            if preserve_audit and 'app_config' in self.prepared:
                # Mirror delivery state belongs to the retained current audit,
                # not to the older app_config copied from the archive.
                cur.execute('DELETE FROM app_config WHERE config_key LIKE %s', ('audit_mirror:%',))
                if 'app_config' in self.original:
                    cur.execute(f'INSERT INTO app_config(config_key,payload,updated_by) '
                        f'SELECT config_key,payload,updated_by FROM {identifier(self.live)}.app_config WHERE config_key LIKE %s', ('audit_mirror:%',))
            cur.execute("""CREATE TABLE IF NOT EXISTS audit_journal (
                id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                ts_utc DATETIME(3) NOT NULL, action VARCHAR(64) NOT NULL,
                actor VARCHAR(128), module VARCHAR(64), personnel_id VARCHAR(64),
                sme_mois_context CHAR(7), payload JSON
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute('INSERT INTO audit_journal(ts_utc,action,actor,module,payload) VALUES(UTC_TIMESTAMP(3),%s,%s,%s,%s)',
                ('database_restored',resolve_audit_actor(),'system',json.dumps({
                    'detail':'Restauration appliquée à cette base et à tous ses postes',
                    'archive':archive_name,'audit_preserved':bool(preserve_audit),
                    'recovery_session':self.workspace.name})))
        self.prepared=self.table_names(self.new)

    def validate(self):
        with operation_progress((('references', 'Contrôle des clés étrangères'),
                                 ('revision', 'Préparation de la révision partagée'))) as operation:
            return self._validate_with_progress(operation)

    def _validate_with_progress(self,operation):
        operation.start('references')
        from sme_gend.services.database_maintenance import check_owned_barrier
        check_owned_barrier(self.settings, self.conn)
        with self.conn.cursor() as cur:
            cur.execute('SELECT TABLE_NAME,CONSTRAINT_NAME,COLUMN_NAME,REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME '
                'FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL '
                'ORDER BY TABLE_NAME,CONSTRAINT_NAME,ORDINAL_POSITION',(self.new,))
            groups={}
            for table,constraint,column,schema,parent,parent_column in cur.fetchall():
                if schema!=self.new:raise RuntimeError('Référence externe détectée dans les tables préparées.')
                groups.setdefault((table,constraint,parent),[]).append((column,parent_column))
            operation.update('references', current=0, total=len(groups))
            for index, ((table,constraint,parent),columns) in enumerate(groups.items(), 1):
                join=' AND '.join(f'c.{identifier(c)}=p.{identifier(p)}' for c,p in columns)
                filled=' AND '.join(f'c.{identifier(c)} IS NOT NULL' for c,p in columns)
                cur.execute(f'SELECT 1 FROM {identifier(table)} c WHERE {filled} AND NOT EXISTS '
                    f'(SELECT 1 FROM {identifier(parent)} p WHERE {join}) LIMIT 1')
                if cur.fetchone():raise RuntimeError('Archive incohérente : une clé étrangère ne retrouve pas son parent.')
                operation.update('references', current=index, total=len(groups), detail=f'{table} → {parent}')
            operation.finish('references')
            operation.start('revision')
            if 'app_config' in self.prepared:
                token=uuid4().hex
                cur.execute('INSERT INTO app_config (config_key,payload,updated_by) VALUES (%s,%s,%s) '
                    'ON DUPLICATE KEY UPDATE payload=VALUES(payload),updated_by=VALUES(updated_by),updated_at=CURRENT_TIMESTAMP(3)',
                    ('shared_data_revision',json.dumps(dict(token=token,reason='full_restore')),'restore'))
        self.record('validated')
        operation.finish('revision')

    def rename_sql(self,reverse=False):
        moves=[]
        if reverse:
            moves.extend((self.live,t,self.new,t) for t in self.prepared)
            moves.extend((self.old,t,self.live,t) for t in self.original)
        else:
            moves.extend((self.live,t,self.old,t) for t in self.original)
            moves.extend((self.new,t,self.live,t) for t in self.prepared)
        return 'RENAME TABLE '+', '.join(f'{identifier(a)}.{identifier(b)} TO {identifier(c)}.{identifier(d)}' for a,b,c,d in moves)+';'

    def publish(self):
        from sme_gend.services.database_maintenance import check_owned_barrier
        check_owned_barrier(self.settings, self.conn)
        if not self.prepared:return
        if self.table_names(self.live)!=self.original or self.table_names(self.new)!=self.prepared:
            raise RuntimeError('Le schéma a changé depuis la préparation ; bascule refusée.')
        forward=self.rename_sql();reverse=self.rename_sql(True)
        write_recovery(self.workspace/'rollback_mysql.sql',('SET FOREIGN_KEY_CHECKS=0;\n'+reverse+'\n').encode('utf-8'))
        self.record('switching')
        self.uncertain=True
        try:
            with self.conn.cursor() as cur:cur.execute(forward)
        except pymysql.MySQLError as exc:
            # A server error acknowledges the failure of this atomic statement.
            # Transport errors can hide a successful switch and require a probe.
            if exc.args and exc.args[0] not in (0,2006,2013,2055):self.uncertain=False
            raise
        self.published=True;self.uncertain=False
        with self.conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s '
                'AND REFERENCED_TABLE_SCHEMA IS NOT NULL AND REFERENCED_TABLE_SCHEMA<>%s',(self.live,self.live))
            if cur.fetchone()[0]:raise RuntimeError('Clés étrangères mal rattachées après la bascule ; retour requis.')
        self.record('switched')

    def rollback(self):
        if self.uncertain:
            # A lost acknowledgement can follow a successful atomic statement.
            # Inspect on a NEW connection; never drop either copy on uncertainty.
            try:self.conn.ping(reconnect=False)
            except Exception:
                try:self.conn.close()
                except Exception:pass
                params=MySQLConnection(self.settings).connect_params();params['autocommit']=True
                self.conn=pymysql.connect(**params)
                with self.conn.cursor() as cur:
                    from sme_gend.services.database_maintenance import _owner
                    owner = _owner.get()
                    cur.execute('SELECT IS_USED_LOCK(%s)',(self.lock,))
                    if owner is None or cur.fetchone()[0] != owner.connection_id:
                        raise RuntimeError('Verrou de récupération indisponible ; maintenir la maintenance.')
                    from sme_gend.services.database_maintenance import worker_lock_name
                    cur.execute('SELECT GET_LOCK(%s,0)', (worker_lock_name(self.settings),))
                    if cur.fetchone()[0] != 1:
                        raise RuntimeError('Traitement précédent encore actif ; récupération manuelle nécessaire.')
                    cur.execute('SET FOREIGN_KEY_CHECKS=0')
                    cur.execute('SET SESSION lock_wait_timeout=15')
            with self.conn.cursor() as cur:
                cur.execute('SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s',(self.new,))
                remaining={row[0] for row in cur.fetchall()}
            if remaining==set(self.prepared):self.published=False
            elif not remaining:self.published=True
            else:raise RuntimeError('État de bascule indéterminé ; maintenir tous les postes fermés et utiliser le journal de récupération.')
            self.uncertain=False
        if self.published:
            with self.conn.cursor() as cur:cur.execute(self.rename_sql(True))
            self.published=False
        self.record('rolled_back')

    def close(self):
        if self.conn is not None:
            self.conn.close()  # Barrier belongs to the outer maintenance context.
        # Both copies remain available for recovery/inspection. No automatic
        # DROP DATABASE is issued by the restore path, even on failure.


@contextmanager
def prepared_restore(settings,statements,workspace,counts=None):
    from sme_gend.services.database_maintenance import centralized_maintenance
    from sme_gend.repositories.mysql_connection import schema_metadata_lock
    with schema_metadata_lock(settings), centralized_maintenance(settings) as lease:
        prepared=PreparedRestore(settings,workspace)
        try:
            prepared.prepare(statements,counts)
            yield prepared
        except BaseException:
            if prepared.published or prepared.uncertain:
                lease.keep_blocked()
            raise
        finally:
            try:
                prepared.close()
            except BaseException:
                lease.keep_blocked()
                raise
