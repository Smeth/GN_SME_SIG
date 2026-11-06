"""Transactional automatic jobs: stable read-only plans, conflicts and one commit signal."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass,field
from functools import wraps
import hashlib,inspect,json,re
from datetime import datetime
from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_database_key,has_import_transaction
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_reference_write import shared_reference_write,mark_reference_write

TABLES=('ref_structure','ref_grade','ref_position_admin','ref_fonction','ref_fonction_structure',
    'ref_subdivision_noeud','ref_subdivision_arme','ref_diplome','agent','sme_periode',
    'carriere_situation','agent_diplome','agent_passation_grade','agent_affectation',
    'structure_organigram_node','mouvement_effectif','etl_erreur','app_config')
CONFIGS=('liste_present_layout','fonction_priority_order','postes_tete_exclus','sme_workspace',
    'active_month_revision','detaches_config','limite_age_config','soit_transmis_config','ui_view_state')
AGENT_FIELDS=('id','legacy_access_id','noms','date_naissance','lieu_naissance','matricule_militaire','matricule_solde')


@dataclass(frozen=True,slots=True)
class AutomaticPlan:
    action:str
    key:str=field(repr=False)
    database:tuple=field(repr=False)
    state:tuple=field(repr=False)
    @property
    def token(self):return fingerprint((self.action,self.key,self.database,self.state))
    def summary(self):
        return dict(mode='apercu',operation=self.action,revision=self.token,
            tables_examined={name:count for name,_,count,_ in self.state},writes_applied=False)


@dataclass
class _JobScope:
    database:tuple
    failed:bool=False


_scope=ContextVar('automatic_maintenance_scope',default=None)


def identifier(value):return '`'+str(value).replace('`','``')+'`'


def validate_month(value):
    if not isinstance(value,str) or re.fullmatch(r'\d{4}-\d{2}',value) is None:
        raise ValueError('Mois invalide : utilisez YYYY-MM.')
    try:datetime.strptime(value,'%Y-%m')
    except ValueError as error:raise ValueError('Mois invalide : utilisez YYYY-MM.') from error
    return value


def active_month(settings):
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    month=load_active_month_snapshot(settings).active_month
    if not month:raise ValueError('Indiquez --mois explicitement : aucun mois ouvert unique.')
    return validate_month(month)


def _capture(cur,*,locking):
    cur.execute('SELECT TABLE_NAME,ENGINE,TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()')
    available={name:(str(engine).upper(),kind) for name,engine,kind in cur.fetchall() if name in TABLES}
    if not {'agent','carriere_situation','sme_periode','app_config'}<=available.keys():
        raise RuntimeError('Schema incomplet : initialisez cette base avec l application avant la maintenance.')
    state=[]
    for table in TABLES:
        if table not in available:continue
        if available[table]!=('INNODB','BASE TABLE'):raise RuntimeError('Maintenance transactionnelle : tables InnoDB requises.')
        cur.execute('SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_DEFAULT,EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION',(table,))
        columns=cur.fetchall();names=[row[0] for row in columns]
        if table=='agent':names=[name for name in names if name in AGENT_FIELDS]
        if table=='app_config':names=['config_key','payload']
        types={row[0]:str(row[1]).lower() for row in columns}
        expressions=[]
        for name in names:
            column=identifier(name);kind=types.get(name,'')
            if any(token in kind for token in ('binary','blob','bit(')):column='HEX('+column+')'
            elif kind.startswith(('decimal','numeric')):column='CAST('+column+' AS CHAR)'
            expressions.append(column)
        schema=fingerprint((available[table],tuple(tuple(row) for row in columns),tuple(names)))
        where='';params=()
        if table=='app_config':where=' WHERE config_key IN ('+','.join(['%s']*len(CONFIGS))+')';params=CONFIGS
        order='config_key' if table=='app_config' else 'id'
        cur.execute('SELECT JSON_ARRAY('+','.join(expressions)+') FROM '+identifier(table)+where+' ORDER BY '+identifier(order)+(' FOR UPDATE' if locking else ''),params)
        digest=hashlib.sha256();count=0
        while True:
            rows=cur.fetchmany(500)
            if not rows:break
            for row in rows:
                value=json.loads(row[0]) if isinstance(row[0],(str,bytes)) else row[0]
                encoded=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
                digest.update(len(encoded).to_bytes(8,'big'));digest.update(encoded);count+=1
        state.append((table,schema,count,digest.hexdigest()))
    return tuple(state)


def capture_state(settings):
    if has_import_transaction(settings):raise RuntimeError('Preparez le plan avant la transaction d ecriture.')
    with MySQLConnection(settings).connect(read_only=True) as conn:
        try:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
                return _capture(cur,locking=False)
        finally:conn.rollback()


def call_identity(function,settings,args,kwargs):
    bound=inspect.signature(function).bind(settings,*args,**kwargs);bound.apply_defaults()
    values={name:value for name,value in bound.arguments.items() if name!='settings'}
    for name in ('mois','keep_from_mois'):
        if values.get(name) is not None:validate_month(values[name])
    action=function.__module__+'.'+function.__name__
    return action,fingerprint(values)


def prepare_call(settings,function,**kwargs):
    body=getattr(function,'__wrapped__',function)
    action,key=call_identity(body,settings,(),kwargs)
    return AutomaticPlan(action,key,mysql_database_key(settings),capture_state(settings))


def assert_permission(action):
    from sme_gend.services.auth_guard import session_has_permission
    permissions=['system.settings']
    if any(word in action for word in ('reconcile_carriere','project_carriere','resync_fk')):
        permissions+=['system.import','effectifs.edition','effectifs.sme_dpf']
    if not any(session_has_permission(permission) for permission in permissions):raise PermissionError('Droits de maintenance insuffisants.')


@contextmanager
def maintenance_operation(settings,action,key,expected=None):
    active=_scope.get();database=mysql_database_key(settings)
    if active is not None:
        if active.database!=database:
            active.failed=True
            raise RuntimeError('Une maintenance ne peut pas ecrire dans une autre base.')
        try:
            if expected is not None:
                if isinstance(expected,AutomaticPlan) and (expected.action,expected.key,expected.database)!=(action,key,database):raise ValueError('Plan imbrique incompatible.')
                with MySQLConnection(settings).connect() as conn:
                    with conn.cursor() as cur:current=AutomaticPlan(action,key,database,_capture(cur,locking=True))
                if current.token!=(expected.token if isinstance(expected,AutomaticPlan) else expected):raise ReferenceConflictError('Plan imbrique modifie ; lot annule.')
            yield
        except BaseException:active.failed=True;raise
        return
    assert_permission(action)
    if isinstance(expected,AutomaticPlan):
        if (expected.action,expected.key,expected.database)!=(action,key,database):raise ValueError('Plan de maintenance incompatible avec cette operation ou cette base.')
    elif expected is not None and (not isinstance(expected,str) or re.fullmatch('[0-9a-f]{64}',expected) is None):
        raise ValueError('Revision de maintenance invalide.')
    if expected is None and not has_import_transaction(settings):expected=AutomaticPlan(action,key,database,capture_state(settings))
    with shared_reference_write(settings):
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                before=_capture(cur,locking=True)
                # In a caller-owned REPEATABLE READ transaction, the job body
                # must not calculate from an older nonlocking snapshot after
                # we just checked and locked current values.
                if _capture(cur,locking=False)!=before:
                    raise ReferenceConflictError('La transaction contient une lecture ancienne. Annulez et rechargez avant la maintenance.')
                current=AutomaticPlan(action,key,database,before)
                if expected is not None and current.token!=(expected.token if isinstance(expected,AutomaticPlan) else expected):
                    raise ReferenceConflictError('Les donnees ont ete modifiees depuis le plan. Aucune maintenance appliquee ; regenerez et verifiez le plan.')
                scope=_JobScope(database);token=_scope.set(scope)
                try:
                    yield
                    if scope.failed:raise RuntimeError('Une etape de maintenance a echoue ; le lot entier est annule.')
                    mark_reference_write(_capture(cur,locking=True)!=before)
                finally:_scope.reset(token)


def automatic_write(function):
    @wraps(function)
    def run(settings,*args,expected=None,**kwargs):
        try:
            action,key=call_identity(function,settings,args,kwargs)
        except BaseException:
            active=_scope.get()
            if active is not None:active.failed=True
            raise
        # DDL is a separate phase, never silently admitted into this transaction.
        if kwargs.get('drop_legacy'):
            active=_scope.get()
            if active is not None:active.failed=True
            raise ValueError('La suppression legacy doit etre lancee dans son etape separee avec sauvegarde et maintenance confirmee.')
        with maintenance_operation(settings,action,key,expected):
            return function(settings,*args,**kwargs)
    return run
