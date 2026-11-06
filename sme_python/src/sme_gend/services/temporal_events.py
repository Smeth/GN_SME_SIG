"""Shared date ownership, monthly eligibility and position projection."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps
from inspect import signature
from types import SimpleNamespace

from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month, ensure_sme_periode
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.services.sme_editing_policy import is_statut_ferme
from sme_gend.repositories.mysql_connection import MySQLConnection

POSITION_SCOPE=ContextVar('temporal_position_scope',default=None)
PERIOD_OVERRIDE=ContextVar('temporal_period_override',default=None)
TABLES={'agent_mission':('date_debut','date_fin','Mission'),
        'agent_malade':('date_debut','date_fin','Malade'),
        'agent_desertion':('date_desertion',None,'Déserteur'),
        'parcours_stage':('date_debut','date_fin',None),
        'agent_conge':('date_debut','date_fin',None)}


def periods(cur,*,lock=True):
    cur.execute('SELECT id,annee_mois,statut FROM sme_periode'+(' FOR SHARE' if lock else ''))
    return [(pid,canonical_sme_mois_yyyy_mm(str(month)),status) for pid,month,status in cur.fetchall()]


def event_period(cur,stamp):
    stamp=parse_access_date(stamp)
    if stamp is None: return None
    month=stamp.strftime('%Y-%m')
    matches=[(pid,status) for pid,m,status in periods(cur) if m==month]
    if len(matches)>1: raise ValueError(f'Plusieurs périodes correspondent à {month} ; vérifiez le catalogue des mois.')
    if matches: return matches[0][0]
    return ensure_sme_periode(cur.connection,month,statut='Préparé')


def eligibility(settings,cur,alias,start,end,*,period_col='sme_periode_id',manual=None):
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    month=canonical_sme_mois_yyyy_mm(resolve_export_mois(settings) or '')
    if not month: return '1=1',()
    rows=periods(cur,lock=False)
    ids=[pid for pid,m,status in rows if m==month]
    owned=f'{alias}.{period_col} IN ('+','.join(['%s']*len(ids))+')' if ids else '0=1'
    date_filter=f'({alias}.{start} IS NULL OR {alias}.{start}<=%s)'
    params=[last_day_of_month(month)]
    if end:
        date_filter+=f' AND ({alias}.{end} IS NULL OR {alias}.{end}>=%s)'
        params.append(first_day_of_month(month))
    if manual is None:
        manual=f"({alias}.import_lot_id IS NULL OR {alias}.source_ligne_legacy LIKE '%%:saisie:%%')"
    scope=f'({owned} OR (({manual}) AND {alias}.{start} IS NOT NULL))'
    return f'({scope}) AND {date_filter}',tuple(ids)+tuple(params)


def _resolve_position(cur,label):
    cur.execute('SELECT id FROM ref_position_admin WHERE UPPER(TRIM(libelle))=UPPER(%s) LIMIT 1',(label,))
    row=cur.fetchone()
    return int(row[0]) if row else None


def project_positions(settings,aid,since,*,removed_kind=None,only_carriere_id=None):
    scope=POSITION_SCOPE.get()
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            closed_months={m for _,m,status in periods(cur) if is_statut_ferme(status or '')}
            changes=[]
            cur.execute('SELECT c.id,p.annee_mois,c.date_fin,c.position_admin_libelle FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=%s',(aid,))
            careers=cur.fetchall()
            cur.execute('SELECT position_admin,date_debut,date_fin FROM agent_position_event WHERE agent_id=%s ORDER BY date_debut DESC,id DESC',(aid,))
            events=cur.fetchall()
            for cid,raw_month,end,current in careers:
                if only_carriere_id is not None and str(cid)!=str(only_carriere_id):continue
                month=canonical_sme_mois_yyyy_mm(str(raw_month))
                if not month: continue
                ref=last_day_of_month(month)
                if parse_access_date(end): ref=min(ref,parse_access_date(end))
                chosen=next((label for label,start,finish in events if start<=ref and (finish is None or finish>=ref)),None)
                if chosen is None:
                    if scope and scope.before is not None and str(cid)==str(scope.before.id):
                        chosen=scope.before.position_admin or 'Présent'
                    elif removed_kind and current==removed_kind and ref>=since:
                        chosen='Présent'
                    else: continue
                if ref<since and not(scope and scope.before is not None and str(cid)==str(scope.before.id)): continue
                if chosen==current: continue
                cur.execute('UPDATE carriere_situation SET position_admin_libelle=%s,position_admin_id=%s WHERE id=%s',
                            (chosen,_resolve_position(cur,chosen),cid))
                changes.append({'carriere_id':cid,'month':month,'closed':month in closed_months,
                                'before':{'position_admin':current},'after':{'position_admin':chosen}})
    if changes:
        from sme_gend.services.temporal_projection_audit import log_temporal_projection
        log_temporal_projection(aid,'position_admin',since,changes)


def record_position(settings,aid,label,start,end=None,*,reference=None,baseline='Présent'):
    start=parse_access_date(start);end=parse_access_date(end)
    if start is None: return
    if end and end<start: raise ValueError('La date de fin doit être postérieure ou égale au début.')
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            event_period(cur,start)
            if end:
                event_period(cur,end)
                event_period(cur,end+timedelta(days=1))
            if reference:
                cur.execute('DELETE FROM agent_position_event WHERE agent_id=%s AND reference_decision=%s',(aid,reference))
            cur.execute('INSERT INTO agent_position_event(agent_id,position_admin,date_debut,date_fin,mois_sme,reference_decision) VALUES(%s,%s,%s,%s,%s,%s)',
                        (aid,label,start,end,start.strftime('%Y-%m'),reference))
            if end:
                cur.execute('INSERT INTO agent_position_event(agent_id,position_admin,date_debut,mois_sme,reference_decision) VALUES(%s,%s,%s,%s,%s)',
                    (aid,baseline if baseline!=label else 'Présent',end+timedelta(days=1),(end+timedelta(days=1)).strftime('%Y-%m'),reference))
    scope=POSITION_SCOPE.get()
    if scope: scope.handled=True
    project_positions(settings,aid,start)


@contextmanager
def position_edit(settings,before):
    scope=SimpleNamespace(settings=settings,before=before,handled=False)
    token=POSITION_SCOPE.set(scope)
    try:
        yield scope
        if not scope.handled and scope.before is not None:
            from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
            after=MySQLAgentRepository(settings).fetch_by_id(scope.before.id)
            if after and after.position_admin!=scope.before.position_admin:
                plain_position_change(settings,scope.before,after,force=True)
    finally: POSITION_SCOPE.reset(token)


def plain_position_change(settings,before,after,*,force=False):
    if before.position_admin==after.position_admin or (POSITION_SCOPE.get() and not force): return
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
    month=canonical_sme_mois_yyyy_mm(resolve_liste_view_mois(settings) or '')
    if not month: raise ValueError('Un mois SME est nécessaire pour changer la position.')
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT agent_id FROM carriere_situation WHERE id=%s',(int(after.id),))
            aid=cur.fetchone()[0]
    record_position(settings,aid,after.position_admin or 'Présent',first_day_of_month(month),baseline=before.position_admin or 'Présent')


def _table_rows(cur,table,aid):
    cur.execute(f'SELECT * FROM {table} WHERE agent_id=%s ORDER BY id FOR UPDATE',(aid,))
    names=[d[0] for d in cur.description]
    records = [dict(zip(names, row)) for row in cur.fetchall()]
    return {row['id']: row for row in records}


def _event_position(table, row):
    if table == 'parcours_stage':
        return {'LOCAL': 'StageLocal', 'EXTERIEUR': 'StageEx', 'INTERNE': 'Stage interne'}[row['type_stage_code']]
    if table == 'agent_conge':
        return {'ADMIN': 'CongéAdmin', 'EXPECTATIVE': 'CongéExp'}.get(row['type_conge_code'], 'Congé')
    return TABLES[table][2]


def temporal_write(table,method):
    contract=signature(method)
    @wraps(method)
    def write(self,*args,**kwargs):
        from sme_gend.services.personnel_service import _personnel_write
        from sme_gend.services.sensitive_data_policy import assert_may_edit_personnel
        assert_may_edit_personnel()
        bound=contract.bind(self,*args,**kwargs).arguments
        with _personnel_write(self._settings,reason='dated_event'):
            with self._connect() as conn:
                with conn.cursor() as cur:
                    legacy=next((bound[k] for k in ('person_id','legacy_id','legacy_access_id') if k in bound),None)
                    if legacy is not None: aid=self._agent_id(cur,legacy) if hasattr(self,'_agent_id') else None
                    else:
                        event_id=next((v for k,v in bound.items() if k.endswith('_id') and k!='self'),None)
                        cur.execute(f'SELECT agent_id FROM {table} WHERE id=%s',(event_id,)); row=cur.fetchone(); aid=row[0] if row else None
                    if not aid and legacy is not None:
                        cur.execute('SELECT id FROM agent WHERE legacy_access_id=%s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1',(legacy,legacy));row=cur.fetchone();aid=row[0] if row else None
                    if not aid: return method(self,*args,**kwargs)
                    old=_table_rows(cur,table,aid)
                    override=None
                    expected=bound.get('expected')
                    token=getattr(expected,'write_token',None)
                    if token and token[0] in old:
                        original=old[token[0]]
                        if original.get('import_lot_id') is None or ':saisie:' in (original.get('source_ligne_legacy') or ''):
                            override=(self,token[1])
                    marker=PERIOD_OVERRIDE.set(override)
                    try: result=method(self,*args,**kwargs)
                    finally: PERIOD_OVERRIDE.reset(marker)
                    new=_table_rows(cur,table,aid)
                    changes=[key for key in old.keys()|new.keys() if old.get(key)!=new.get(key)]
                    start_col,end_col,kind=TABLES[table]
                    for key in changes:
                        previous=old.get(key); current=new.get(key)
                        old_start=parse_access_date(previous.get(start_col)) if previous else None
                        if old_start: event_period(cur,old_start)
                        ref=f'sgi:auto:{table}:{key}'
                        if current is None:
                            cur.execute('DELETE FROM agent_position_event WHERE agent_id=%s AND reference_decision=%s',(aid,ref))
                            if old_start: project_positions(self._settings,aid,old_start,removed_kind=_event_position(table,previous))
                            continue
                        start=parse_access_date(current.get(start_col));end=parse_access_date(current.get(end_col)) if end_col else None
                        if not start: continue
                        pid=event_period(cur,start)
                        if end: event_period(cur,end)
                        cur.execute(f'UPDATE {table} SET sme_periode_id=%s WHERE id=%s',(pid,key))
                        label=_event_position(table,current)
                        scope=POSITION_SCOPE.get()
                        baseline=scope.before.position_admin if scope and scope.before else 'Présent'
                        record_position(self._settings,aid,label,start,end,reference=ref,baseline=baseline or 'Présent')
                        if old_start and old_start<start: project_positions(self._settings,aid,old_start,removed_kind=label)
                    return result
    return write


def install_temporal_writes(cls,mapping):
    for table,names in mapping.items():
        for name in names: setattr(cls,name,temporal_write(table,getattr(cls,name)))


def project_available_history(settings,careers, *, progress=None):
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    repo=MySQLAgentDossierRepository(settings)
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            for index, (cid,aid) in enumerate(careers, 1):
                components=set()
                for table,component in (('agent_diplome','diplome'),('agent_passation_grade','grade'),('agent_affectation','affectation')):
                    cur.execute(f'SELECT 1 FROM {table} WHERE agent_id=%s LIMIT 1',(aid,))
                    if cur.fetchone():components.add(component)
                if components:repo.sync_carriere_from_dossier(aid,str(cid),components=frozenset(components))
                project_positions(settings,aid,first_day_of_month(repo._carriere_cutoff(str(cid)).strftime('%Y-%m')),only_carriere_id=cid)
                if progress is not None:
                    progress(index, len(careers))
