"""Durable delivery of optional JSONL mirrors, using existing app_config JSON.

Queue inserts share the audit/business connection. Only the owning local profile
delivers its entries. A recorded file offset avoids rescanning entire logs.
"""
from datetime import datetime,timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
from uuid import uuid4

from sme_gend.repositories.mysql_connection import MySQLConnection,after_mysql_commit,has_import_transaction,CommittedMirrorError
from sme_gend.services.local_audit_mirror import locked_mirror_stream,write_all
from sme_gend.services.mysql_private_options import _restrict_folder

PREFIX='audit_mirror:'
EVENT_ID='_mirror_event_id'
_UUID=re.compile(r'[0-9a-f]{32}')
_LOG=logging.getLogger(__name__)


def _node_file():
    from sme_gend.config.user_paths import user_application_data_dir
    return user_application_data_dir()/'audit_delivery'/'node_id'


def node_id(*,create=True):
    path=_node_file()
    if not create and not path.exists():return None
    path.parent.mkdir(parents=True,exist_ok=True)
    _restrict_folder(path.parent)
    with locked_mirror_stream(path) as stream:
        stream.seek(0);value=stream.read().strip()
        if not value:
            if not create:raise RuntimeError('Identifiant du profil de journaux incomplet ; vérifier le dossier audit_delivery.')
            value=uuid4().hex.encode('ascii');write_all(stream,value+b'\n');os.fsync(stream.fileno())
        text=value.decode('ascii')
        if not _UUID.fullmatch(text):raise RuntimeError('Identifiant du profil de journaux invalide ; récupération à contrôler.')
        return text


def route_path(route):
    if not isinstance(route,dict):raise ValueError('Destination de journal invalide.')
    kind=route.get('kind')
    if kind=='personnel':
        month=route.get('month','')
        if not re.fullmatch(r'\d{4}-\d{2}',month):raise ValueError('Mois de journal invalide.')
        datetime.strptime(month+'-01','%Y-%m-%d')
        from sme_gend.services.audit_log import _audit_dir
        return _audit_dir()/f'personnel_audit_{month}.jsonl'
    if kind=='activity':
        from sme_gend.services.audit_log import _user_activity_path
        return _user_activity_path()
    if kind=='sensitive':
        from sme_gend.services.sensitive_audit_log import _local_log_path
        return _local_log_path()
    if kind=='documents':
        from sme_gend.services.doc_gen_log import _log_path
        return _log_path()
    raise ValueError('Type de journal non autorisé.')


def route_for(path):
    path=Path(path)
    kinds={'user_activity.jsonl':'activity','sensitive_audit_log.jsonl':'sensitive','doc_gen_log.jsonl':'documents'}
    match=re.fullmatch(r'personnel_audit_(\d{4}-\d{2})\.jsonl',path.name)
    route={'kind':'personnel','month':match[1]} if match else {'kind':kinds.get(path.name)}
    resolved=path.resolve()
    if route.get('kind') and route_path(route).resolve()==resolved:
        return route
    # The destination factory is authoritative. Tests and portable profiles may
    # legitimately supply a different basename, without permitting arbitrary paths.
    for kind in ('sensitive','activity','documents'):
        candidate={'kind':kind}
        if route_path(candidate).resolve()==resolved:return candidate
    raise ValueError('Le journal ne correspond pas à sa destination autorisée.')


def _bytes(value):
    return json.dumps(value,ensure_ascii=False,default=str,allow_nan=False,separators=(',',':')).encode('utf-8')


def _freeze_entries(entries):
    frozen=[];seen=set()
    for entry in entries:
        copied=json.loads(_bytes(entry))
        if not isinstance(copied,dict):raise ValueError('Ligne de journal invalide.')
        token=copied.setdefault(EVENT_ID,uuid4().hex)
        if not isinstance(token,str) or not _UUID.fullmatch(token) or token in seen:raise ValueError('Identifiant de livraison ambigu.')
        seen.add(token);frozen.append(copied)
    return frozen


def enqueue_on_connection(conn,path,entries):
    """Called before the caller commits; no independent business commit."""
    entries=_freeze_entries(entries)
    if not entries:return []
    route=route_for(path);owner=node_id();keys=[]
    initial_position=None
    if route['kind']=='documents':
        # Document generation retains its local fallback on DB failure. Capture
        # the old suffix BEFORE commit so a lost acknowledgement cannot produce
        # both a fallback line and its queued duplicate on restart.
        try:
            with locked_mirror_stream(path,create=False) as existing:
                initial_position=_position(existing)
        except OSError:
            initial_position=dict(offset=0,file_id=None,anchor=None)
    # Bound individual SQL queue values; very large single audit entries remain
    # subject to the same MySQL packet limits as the original audit itself.
    chunks=[];chunk=[];size=0
    for entry in entries:
        length=len(_bytes(entry))
        if chunk and size+length>512*1024:chunks.append(chunk);chunk=[];size=0
        chunk.append(entry);size+=length
    if chunk:chunks.append(chunk)
    with conn.cursor() as cur:
        for items in chunks:
            key=PREFIX+uuid4().hex
            payload=dict(version=1,node=owner,route=route,entries=items,position=initial_position,
                queued_at=datetime.now(timezone.utc).isoformat())
            cur.execute('INSERT INTO app_config(config_key,payload,updated_by) VALUES (%s,%s,%s)',
                (key,_bytes(payload).decode('utf-8'),'audit_delivery'))
            keys.append(key)
    return keys


def schedule_delivery(settings,keys):
    if not keys:return
    def deliver():
        try:
            for key in keys:deliver_key(settings,key)
        except Exception as exc:
            raise CommittedMirrorError(
                'Données et journal MySQL enregistrés ; copie locale en attente de reprise automatique. '
                'Ne recommencez pas la modification. Vérifiez l’accès au dossier des journaux.'
            ) from exc
    after_mysql_commit(deliver)


def _load(settings,key,owner):
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT payload FROM app_config WHERE config_key=%s',(key,));row=cur.fetchone()
    if not row:return None
    body=json.loads(row[0]) if isinstance(row[0],(str,bytes,bytearray)) else row[0]
    if not isinstance(body,dict) or body.get('version')!=1 or isinstance(body.get('version'),bool):
        raise ValueError('Version de livraison persistante non prise en charge.')
    if body.get('node')!=owner:return None
    route_path(body.get('route'))
    entries=body.get('entries')
    if not isinstance(entries,list) or not entries:raise ValueError('Livraison persistante vide ou invalide.')
    if any(not isinstance(entry,dict) or not isinstance(entry.get(EVENT_ID),str) or not _UUID.fullmatch(entry[EVENT_ID]) for entry in entries):
        raise ValueError('Événement persistant invalide.')
    if len({entry[EVENT_ID] for entry in entries})!=len(entries):raise ValueError('Livraison persistante dupliquée.')
    return body


def _flush(stream):
    stream.flush();os.fsync(stream.fileno())


def _repair_tail(stream,path):
    stream.seek(0,os.SEEK_END);end=stream.tell()
    if not end:return
    stream.seek(end-1)
    if stream.read(1)==b'\n':return
    start=end;tail=b''
    while start:
        size=min(start,65536);start-=size;stream.seek(start);tail=stream.read(size)+tail
        found=tail.rfind(b'\n')
        if found>=0:start+=found+1;tail=tail[found+1:];break
    try:
        if not isinstance(json.loads(tail),dict):raise ValueError('not an object')
    except (ValueError,UnicodeDecodeError):
        directory=path.parent/'.audit-recovery';directory.mkdir(exist_ok=True);_restrict_folder(directory)
        fragment=directory/(uuid4().hex+'.part')
        with fragment.open('xb') as output:write_all(output,tail);_flush(output)
        stream.seek(start);stream.truncate();_flush(stream)
        _LOG.warning('Fin de journal interrompue conservée dans un fragment de récupération privé.')
    else:
        stream.seek(0,os.SEEK_END);write_all(stream,b'\n');_flush(stream)


def _position(stream):
    stream.seek(0,os.SEEK_END);offset=stream.tell()
    stat=os.fstat(stream.fileno());stream.seek(max(0,offset-64));anchor=sha256(stream.read(offset-max(0,offset-64))).hexdigest()
    return dict(offset=offset,file_id=f'{stat.st_dev}:{stat.st_ino}',anchor=anchor)


def _resume_offset(stream,position):
    if not isinstance(position,dict):return 0
    offset=position.get('offset');stat=os.fstat(stream.fileno())
    if isinstance(offset,bool) or not isinstance(offset,int) or offset<0:return 0
    if offset>stat.st_size or position.get('file_id')!=f'{stat.st_dev}:{stat.st_ino}':return 0
    start=max(0,offset-64);stream.seek(start)
    return offset if sha256(stream.read(offset-start)).hexdigest()==position.get('anchor') else 0


def _seen_since(stream,offset,wanted):
    """Streaming scan, normally only the suffix since the recorded delivery."""
    stream.seek(offset);buffer=b'';seen=set()
    while True:
        block=stream.read(65536)
        if not block:break
        lines=(buffer+block).split(b'\n');buffer=lines.pop()
        for line in lines:
            if not line.strip():continue
            entry=json.loads(line)
            if not isinstance(entry,dict):raise ValueError('Ligne de journal existante invalide.')
            token=entry.get(EVENT_ID)
            if token in wanted:
                if entry!=wanted[token]:raise ValueError('Identifiant de journal associé à deux contenus.')
                seen.add(token)
    if buffer:raise ValueError('Fin de journal incomplète non réparée.')
    return seen


def deliver_key(settings,key):
    if not re.fullmatch(re.escape(PREFIX)+r'[0-9a-f]{32}',key):raise ValueError('Clé de livraison invalide.')
    if has_import_transaction(settings):raise RuntimeError('Le journal local ne peut précéder le commit métier.')
    owner=node_id(create=False)
    if owner is None:return 0
    body=_load(settings,key,owner)
    if body is None:return 0
    path=route_path(body['route'])
    with locked_mirror_stream(path) as stream:
        # A second process may already have delivered/deleted this queue row.
        body=_load(settings,key,owner)
        if body is None:return 0
        _repair_tail(stream,path)
        if body.get('position') is None:
            body['position']=_position(stream)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:cur.execute('UPDATE app_config SET payload=%s WHERE config_key=%s',(_bytes(body).decode('utf-8'),key))
        wanted={entry[EVENT_ID]:entry for entry in body['entries']}
        seen=_seen_since(stream,_resume_offset(stream,body['position']),wanted)
        stream.seek(0,os.SEEK_END)
        for token,entry in wanted.items():
            if token not in seen:write_all(stream,_bytes(entry)+b'\n')
        _flush(stream)
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:cur.execute('DELETE FROM app_config WHERE config_key=%s',(key,))
    return len(wanted)


def enabled_mirror_kinds():
    from sme_gend.services import audit_log,sensitive_audit_log,doc_gen_log
    enabled=set()
    if audit_log._json_mirror_enabled():enabled.update(('personnel','activity'))
    if sensitive_audit_log._json_mirror_enabled():enabled.add('sensitive')
    if doc_gen_log._json_mirror_enabled():enabled.add('documents')
    return enabled


def recover_pending(settings,*,limit=100,enabled_kinds=None):
    allowed=None if enabled_kinds is None else frozenset(enabled_kinds)
    if allowed is not None:
        if not allowed.issubset({'personnel','activity','sensitive','documents'}):
            raise ValueError('Types de journaux à reprendre invalides.')
        if not allowed:return dict(delivered=0,failed=0)
    owner=node_id(create=False)
    if owner is None:return dict(delivered=0,failed=0)
    pattern=PREFIX.replace('=','==').replace('_','=_')+'%'
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            from sme_gend.services.mysql_restore_staging import maintenance_lock_name
            cur.execute("SELECT IS_USED_LOCK(%s)", (maintenance_lock_name(settings),))
            if cur.fetchone()[0] is not None:
                return dict(delivered=0,failed=0,paused=True)
            restriction=""
            parameters=[pattern,owner]
            if allowed is not None:
                kinds=sorted(allowed)
                restriction=" AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.route.kind')) IN ("+','.join(['%s']*len(kinds))+")"
                parameters.extend(kinds)
            parameters.append(max(1,min(int(limit),1000)))
            cur.execute("SELECT config_key FROM app_config WHERE config_key LIKE %s ESCAPE '=' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(payload,'$.node'))=%s"+restriction+
                " ORDER BY JSON_UNQUOTE(JSON_EXTRACT(payload,'$.queued_at')),config_key LIMIT %s",tuple(parameters))
            keys=[row[0] for row in cur.fetchall()]
    delivered=failed=0
    for key in keys:
        try:delivered+=deliver_key(settings,key)
        except Exception:
            failed+=1
            _LOG.warning('Copie locale de journal en attente ; livraison persistante conservée (%s).',key)
    return dict(delivered=delivered,failed=failed)
