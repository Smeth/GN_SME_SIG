"""Portable row bundles for one month, with remapping of shared identities."""
from datetime import date, datetime, time, timezone
from decimal import Decimal
import base64
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_import_transaction
from sme_gend.services.mysql_import_period import normalize_import_mois
from sme_gend.services.month_period_scope import month_period_ids
from sme_gend.services.automatic_position_links import automatic_source, source_match_fields
from sme_gend.services.operation_progress import operation_progress

MEMBER = 'month_backup.json'
EXTRA = ('workflow_task', 'career_alert_ack', 'career_alert_ack_occurrence')
EVENTS = ('agent_diplome', 'agent_distinction', 'agent_passation_grade', 'agent_affectation',
          'agent_mission', 'agent_malade', 'agent_desertion', 'parcours_stage',
          'mouvement_effectif', 'agent_conge', 'agent_position_event', 'carriere_situation')
MEMBERSHIP = 'sme_month_membership'
DATED_MOVEMENTS = 'agent_effectif_movement'
OWNED = frozenset((*EVENTS, *EXTRA, 'import_lot', 'etl_erreur', 'sme_periode', MEMBERSHIP))


def quote(name):
    return '`' + name.replace('`', '``') + '`'


def encode(value):
    if isinstance(value, (date, datetime, time)):
        return {'$date': value.isoformat()}
    if isinstance(value, Decimal):
        return {'$decimal': str(value)}
    if isinstance(value, bytes):
        return {'$bytes': base64.b64encode(value).decode()}
    return value


def decode(value):
    if isinstance(value, dict):
        if set(value) == {'$date'}:
            return value['$date']
        if set(value) == {'$decimal'}:
            return Decimal(value['$decimal'])
        if set(value) == {'$bytes'}:
            return base64.b64decode(value['$bytes'], validate=True)
        raise ValueError('Valeur non reconnue dans la sauvegarde mensuelle.')
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError('Valeur non scalaire dans la sauvegarde mensuelle.')
    return value


def metadata(cur):
    cur.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_KEY,EXTRA FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION")
    schema = {}
    for table, column, key, extra in cur.fetchall():
        schema.setdefault(table, {'columns': [], 'pk': [], 'auto': None, 'fk': []})
        schema[table]['columns'].append(column)
        if key == 'PRI': schema[table]['pk'].append(column)
        if 'auto_increment' in extra: schema[table]['auto'] = column
    cur.execute("SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL")
    for table, column, parent, parent_column in cur.fetchall():
        schema[table]['fk'].append((column, parent, parent_column))
    cur.execute("SELECT TABLE_NAME,INDEX_NAME,COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND NON_UNIQUE=0 AND INDEX_NAME<>'PRIMARY' ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX")
    for table, index, column in cur.fetchall():
        schema[table].setdefault('unique', {}).setdefault(index, []).append(column)
    return schema


def read_rows(cur, table, predicate='1=1', params=(), *, alias=''):
    cur.execute(f'SELECT * FROM {quote(table)} {alias} WHERE {predicate}', params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def ownership(cur, month, ids, schema):
    from sme_gend.services.month_purge_plan import month_purge_plan
    result = {}
    def add(table, predicate, params):
        if table in schema: result.setdefault(table, []).append((predicate, params))
    for pid in ids:
        for item in month_purge_plan(pid, month, imported_only=False, target_period_ids=ids):
            add(item.table, item.predicate, item.params)
        add('sme_periode', 'd.id=%s', (pid,))
        add('import_lot', 'd.sme_periode_id=%s', (pid,))
        add('etl_erreur', 'd.import_lot_id IN (SELECT id FROM import_lot WHERE sme_periode_id=%s)', (pid,))
        add(MEMBERSHIP, 'd.sme_periode_id=%s', (pid,))
    for table in EXTRA:
        add(table, "TRIM(REPLACE(d.mois,'-',''))=%s", (month.replace('-',''),))
    return result


def create_month_backup(settings, month, destination):
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission('system.backup'):
        raise PermissionError('Permission de sauvegarde refusée.')
    return _create_month_backup(settings, month, destination)


def _create_month_backup(settings, month, destination):
    """Internal row export; caller must authorize backup or restoration."""
    with operation_progress((('read', 'Ouverture de l’instantané du mois'),
                             ('collect', 'Lecture des données mensuelles'),
                             ('dependencies', 'Collecte des historiques et dépendances'),
                             ('write', 'Création de la sauvegarde mensuelle'),
                             ('publish', 'Enregistrement de la sauvegarde'))) as operation:
        return _create_month_backup_with_progress(settings, month, destination, operation)


def _create_month_backup_with_progress(settings, month, destination, operation):
    operation.start('read')
    month = normalize_import_mois(month)
    with MySQLConnection(settings).connect(read_only=True) as conn:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
            schema = metadata(cur)
            ids = month_period_ids(cur, month)
            if not ids: raise ValueError('Ce mois est absent de la base MySQL.')
            operation.finish('read', detail=f'Mois {month}')
            tables = {}
            owned_tables = ownership(cur, month, ids, schema)
            operation.start('collect', total=len(owned_tables))
            for index, (table, predicates) in enumerate(owned_tables.items(), 1):
                where = ' OR '.join('('+p+')' for p, _ in predicates)
                args = tuple(v for _, values in predicates for v in values)
                tables[table] = sorted(read_rows(cur, table, where, args, alias='d'),
                                       key=lambda row:tuple(row[k] for k in schema[table]['pk']))
                operation.update('collect', current=index, total=len(owned_tables),
                    detail=f'{table} : {len(tables[table]):,} ligne(s) lue(s)')
            operation.finish('collect')
            operation.start('dependencies', detail='Historique des mouvements, diplômes et grades')
            dated_agents = []
            if DATED_MOVEMENTS in schema and MEMBERSHIP in schema:
                from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
                dated_agents = sorted({int(row['agent_id']) for table in ('carriere_situation', MEMBERSHIP)
                    for row in tables.get(table, [])} | {int(row['agent_id']) for row in read_rows(cur,
                    DATED_MOVEMENTS, 'effective_date BETWEEN %s AND %s', (first_day_of_month(month),last_day_of_month(month)))})
                tables[DATED_MOVEMENTS] = []
                for offset in range(0, len(dated_agents), 500):
                    batch = dated_agents[offset:offset + 500]
                    tables[DATED_MOVEMENTS].extend(read_rows(cur, DATED_MOVEMENTS,
                        'agent_id IN (' + ','.join(['%s'] * len(batch)) + ') AND effective_date<=%s',
                        (*batch, last_day_of_month(month))))
            shared_diplomas = []
            shared_passations = []
            auto_sources = []
            from sme_gend.repositories.mysql_agent_repository import _select_liste
            display_sql, display_params = _select_liste(month, settings=settings, cursor=cur)
            cur.execute(display_sql, display_params)
            displayed = {r[0]: (r[17], r[18]) for r in cur.fetchall()}
            for row in tables.get('carriere_situation', []):
                if row['id'] in displayed:
                    row['diplome_libelle'], row['date_obtention'] = displayed[row['id']]
            existing_diplomas = {r['id'] for r in tables.get('agent_diplome', [])}
            placeholders=','.join(['%s']*len(ids))
            latest = read_rows(cur,'agent_diplome',
                "id IN (SELECT (SELECT d2.id FROM agent_diplome d2 FORCE INDEX (idx_agent_diplome_agent) "
                "WHERE d2.agent_id=c.agent_id AND d2.categorie='MILITAIRE' "
                "AND (d2.date_obtention IS NULL OR d2.date_obtention <= LEAST(COALESCE(c.date_fin,'9999-12-31'),LAST_DAY(CONCAT(%s,'-01')))) "
                "ORDER BY d2.date_obtention DESC,d2.id DESC LIMIT 1) FROM carriere_situation c "
                f"WHERE c.sme_periode_id IN ({placeholders}))", (month,*ids))
            for row in latest:
                if row['id'] not in existing_diplomas:
                    row['import_lot_id'] = None
                    tables.setdefault('agent_diplome', []).append(row)
                    shared_diplomas.append(row['id']); existing_diplomas.add(row['id'])
            # A passation is durable history, even if its year has no SME archive.
            existing_grades = {row['id'] for row in tables.get('agent_passation_grade', [])}
            history = read_rows(cur, 'agent_passation_grade',
                'EXISTS (SELECT 1 FROM carriere_situation c WHERE c.agent_id=agent_passation_grade.agent_id '
                f'AND c.sme_periode_id IN ({placeholders}) AND '
                '(date_passation IS NULL OR date_passation<=LEAST(COALESCE(c.date_fin,\'9999-12-31\'),'
                'LAST_DAY(CONCAT(%s,\'-01\')))))', (*ids, month))
            for row in history:
                if row['id'] not in existing_grades:
                    tables.setdefault('agent_passation_grade', []).append(row)
                    shared_passations.append(row['id'])
                    existing_grades.add(row['id'])
            for event in tables.get('agent_position_event', []):
                reference=str(event.get('reference_decision') or '')
                if not reference.startswith('sgi:auto:'): continue
                source_table,source_id,_,_=automatic_source(reference)
                if not any(r['id']==source_id for r in tables.get(source_table, [])):
                    found=read_rows(cur,source_table,'id=%s',(source_id,))
                    if len(found)!=1: raise ValueError('Source de position introuvable.')
                    auto_sources.append({'table':source_table,'row':{k:encode(v) for k,v in found[0].items()}})
            # Add the exact shared dependencies required by the month, never all agents.
            changed = True
            while changed:
                changed = False
                for table, rows in list(tables.items()):
                    for column, parent, parent_col in schema[table]['fk']:
                        if parent not in OWNED and parent not in ('agent', DATED_MOVEMENTS) and not parent.startswith('ref_'):
                            raise ValueError('Dépendance de mois non prise en charge : '+parent)
                        known = {r[parent_col] for r in tables.get(parent, [])}
                        missing = {r[column] for r in rows if r.get(column) is not None} - known
                        values = list(missing)
                        for offset in range(0,len(values),500):
                            chunk = values[offset:offset+500]
                            found = read_rows(cur,parent,quote(parent_col)+' IN ('+','.join(['%s']*len(chunk))+')',tuple(chunk))
                            if len(found)!=len(chunk): raise ValueError('Dépendance manquante dans la base.')
                            if parent=='sme_periode' and any(r['id'] not in ids for r in found):
                                raise ValueError('Une dépendance concerne un autre mois.')
                            tables.setdefault(parent, []).extend(found)
                            changed = True
                            operation.update('dependencies', detail=f'Dépendance {parent} ; {sum(len(rows) for rows in tables.values()):,} ligne(s) collectée(s)')
        conn.rollback()
    operation.finish('dependencies', detail=f'{len(tables)} table(s), {sum(len(rows) for rows in tables.values()):,} ligne(s)')
    operation.start('write')
    payload = {'format': 'sme-month', 'version': 1, 'month': month,
               'created_at': datetime.now(timezone.utc).isoformat(), 'shared_diplomas': shared_diplomas,
               'shared_passations': shared_passations, 'auto_sources': auto_sources,
               'dated_movement_agents': dated_agents,
               'tables': {t: [{k:encode(v) for k,v in r.items()} for r in rows] for t,rows in tables.items()}}
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    from sme_gend.services.safe_paths import atomic_destination
    destination = Path(destination)
    with atomic_destination(destination) as temporary:
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as z:
            z.writestr(MEMBER, data)
            z.writestr('month_sha256.txt', hashlib.sha256(data).hexdigest())
        operation.finish('write', detail=f'{len(data):,} octet(s) et empreinte SHA-256 écrits')
        operation.start('publish')
    operation.finish('publish', detail=f'Mois {month} enregistré')
    return {'month': month, 'counts': {t:len(r) for t,r in tables.items()}, 'path': str(destination)}


def inspect_month_backup(path):
    with ZipFile(path) as z:
        if sorted(z.namelist()) != sorted([MEMBER, 'month_sha256.txt']):
            raise ValueError('Choisissez une sauvegarde mensuelle créée par cette option.')
        if z.getinfo(MEMBER).file_size > 512*1024*1024:
            raise ValueError('Archive mensuelle trop volumineuse.')
        data = z.read(MEMBER)
        if hashlib.sha256(data).hexdigest() != z.read('month_sha256.txt').decode('ascii'):
            raise ValueError('Sauvegarde mensuelle endommagée.')
    p = json.loads(data)
    if p.get('format') != 'sme-month' or p.get('version') != 1:
        raise ValueError('Format mensuel non pris en charge.')
    p['month'] = normalize_import_mois(p['month'])
    if not isinstance(p.get('tables'), dict): raise ValueError('Tables mensuelles invalides.')
    for table, rows in p['tables'].items():
        if table not in OWNED and table not in ('agent', DATED_MOVEMENTS) and not table.startswith('ref_'):
            raise ValueError('Table interdite dans une sauvegarde mensuelle : '+table)
        if not isinstance(rows, list) or any(not isinstance(r,dict) for r in rows):
            raise ValueError('Lignes mensuelles invalides.')
        for row in rows:
            for value in row.values(): decode(value)
    periods = p['tables'].get('sme_periode', [])
    if not periods or any(normalize_import_mois(r['annee_mois']) != p['month'] for r in periods):
        raise ValueError('Les périodes ne correspondent pas au mois annoncé.')
    return p


def validate_rows(payload, schema):
    month = payload['month']
    rows_by_table = {t:[{k:decode(v) for k,v in r.items()} for r in rows]
                     for t,rows in payload['tables'].items()}
    period_ids = {r['id'] for r in rows_by_table['sme_periode']}
    if DATED_MOVEMENTS in rows_by_table:
        from sme_gend.services.access_date_parse import parse_access_date
        from sme_gend.services.mysql_import_period import last_day_of_month
        agents = payload.get('dated_movement_agents', [])
        if (not isinstance(agents, list) or any(type(aid) is not int or aid < 1 for aid in agents)
                or len(set(agents)) != len(agents)
                or not set(agents) <= {row['id'] for row in rows_by_table.get('agent', [])}):
            raise ValueError('Agents du registre daté invalides dans la sauvegarde.')
        seen_days = set()
        for row in rows_by_table[DATED_MOVEMENTS]:
            stamp = parse_access_date(row.get('effective_date'))
            key = (row.get('agent_id'), stamp)
            if (key in seen_days or row.get('agent_id') not in agents or stamp is None
                    or stamp > last_day_of_month(month) or row.get('category') not in ('GAIN','PERTE')):
                raise ValueError('Chronologie des mouvements datés invalide.')
            seen_days.add(key)
    lot_ids = {r['id'] for r in rows_by_table.get('import_lot', [])}
    shared_diplomas = set(payload.get('shared_diplomas', []))
    shared_passations = set(payload.get('shared_passations', []))
    if not shared_passations.issubset({r['id'] for r in rows_by_table.get('agent_passation_grade', [])}):
        raise ValueError('Dépendances de passations invalides.')
    if not shared_diplomas.issubset({r['id'] for r in rows_by_table.get('agent_diplome', [])}):
        raise ValueError('Dépendances de diplomes invalides.')
    dated = {'agent_diplome':'date_obtention', 'agent_distinction':'date_attribution',
             'agent_passation_grade':'date_passation', 'agent_affectation':'date_affectation'}
    for table, rows in rows_by_table.items():
        if table not in schema: raise ValueError('Schéma incompatible : '+table)
        seen = set()
        for row in rows:
            if set(row) != set(schema[table]['columns']):
                raise ValueError('Colonnes incompatibles : '+table)
            key = tuple(row[c] for c in schema[table]['pk'])
            if not key or key in seen: raise ValueError('Clé dupliquée dans '+table)
            seen.add(key)
            if table == MEMBERSHIP and (row.get('is_member') not in (0,1) or row.get('baseline_member') not in (0,1)):
                raise ValueError('Observation et visibilité mensuelle doivent valoir 0 ou 1.')
            if table not in OWNED or table=='sme_periode': continue
            if table == 'agent_passation_grade' and row['id'] in shared_passations:
                from sme_gend.services.access_date_parse import parse_access_date
                from sme_gend.services.mysql_import_period import last_day_of_month
                stamp = parse_access_date(row.get('date_passation'))
                if row.get('date_passation') and (stamp is None or stamp > last_day_of_month(month)):
                    raise ValueError('Passation partagée hors de la période restaurée.')
                continue
            if table=='agent_diplome' and row['id'] in shared_diplomas:
                if row.get('categorie')!='MILITAIRE' or row.get('import_lot_id') is not None:
                    raise ValueError('Diplôme partagé invalide.')
                continue
            valid = False
            if table in dated:
                stamp = row.get(dated[table])
                origin = str(row.get('source' if table=='agent_distinction' else 'obs') or '').split('\n')[0]
                valid = (str(stamp or '')[:7] == month or row.get('import_lot_id') in lot_ids
                         or origin.replace('-','') == ('Import archive '+month).replace('-','')
                         or (table=='agent_affectation' and str(row.get('sme_periode_mois') or '').replace('-','')==month.replace('-','')))
            elif table in EXTRA:
                valid = str(row.get('mois') or '').replace('-','') == month.replace('-','')
            elif table=='agent_position_event':
                valid = str(row.get('mois_sme') or '').replace('-','') == month.replace('-','')
            elif table=='etl_erreur': valid = row.get('import_lot_id') in lot_ids
            else: valid = row.get('sme_periode_id') in period_ids
            if not valid: raise ValueError('Une ligne concerne un autre mois : '+table)
    for source in payload.get('auto_sources', []):
        table=source.get('table')
        if table not in EVENTS or table not in schema or not isinstance(source.get('row'),dict):
            raise ValueError('Source automatique invalide.')
        if set(source['row'])!=set(schema[table]['columns']):
            raise ValueError('Schéma de source automatique incompatible.')
        for value in source['row'].values():decode(value)
    # All dependencies must be present in the archive; no arbitrary reference into the target.
    for table, rows in rows_by_table.items():
        for column,parent,parent_col in schema[table]['fk']:
            known = {r[parent_col] for r in rows_by_table.get(parent, [])}
            if any(r[column] is not None and r[column] not in known for r in rows):
                raise ValueError('Dépendance absente : '+table+'.'+column)
    return rows_by_table


def shared_match(cur, table, row, meta, schema):
    candidates = []
    if table=='agent_diplome':
        fields=['agent_id','categorie','libelle_libre','date_obtention']
        found=read_rows(cur,table,' AND '.join(quote(c)+' <=> %s' for c in fields),tuple(row[c] for c in fields))
        return found[0] if found else None
    if table=='agent':
        for field in ('legacy_access_id','matricule_militaire','matricule_solde'):
            if row.get(field): candidates.append(([field], [row[field]]))
        if 'agent_identity_alias' in schema:
            cur.execute('SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s', (row['legacy_access_id'],))
            alias = cur.fetchone()
            if alias: candidates.append((['id'], [alias[0]]))
    else:
        candidates = [(cols,[row[c] for c in cols]) for cols in meta.get('unique',{}).values()
                      if all(row.get(c) is not None for c in cols)]
        if not candidates and meta['auto'] is None:
            candidates = [(meta['pk'],[row[c] for c in meta['pk']])]
    found = {}
    for cols, values in candidates:
        for existing in read_rows(cur, table, ' AND '.join(quote(c)+'=%s' for c in cols), tuple(values)):
            found[tuple(existing[c] for c in meta['pk'])] = existing
    if len(found)>1:
        raise ValueError('Rapprochement ambigu : '+table+'. Aucune restauration validée.')
    return next(iter(found.values()), None)


def _movement_evidence(row):
    return tuple(str(row.get(key) or '') for key in ('category','movement_kind','effective_date','reference_note','details'))


def _check_dated_timeline(cur, month, rows, original_agent_id, target_agent_id):
    """Current global facts cannot be silently corrected by restoring a month."""
    from sme_gend.services.mysql_import_period import last_day_of_month
    archived = {str(row['effective_date']): row for row in rows.get(DATED_MOVEMENTS, [])
                if row['agent_id'] == original_agent_id}
    current = read_rows(cur, DATED_MOVEMENTS, 'agent_id=%s AND effective_date<=%s',
                        (target_agent_id,last_day_of_month(month)))
    for row in current:
        matching = archived.get(str(row['effective_date']))
        if matching is None or _movement_evidence(matching) != _movement_evidence(row):
            raise ValueError('Les mouvements réels déjà enregistrés pour cet agent diffèrent de la sauvegarde. '
                             'Régularisez explicitement leur historique avant de restaurer ce mois ; aucun fait daté n’a été remplacé.')


def _check_dated_correction_evidence(cur, row):
    """A month archive cannot resurrect a globally deleted/moved/corrected fact."""
    stamp = str(row['effective_date'])
    cur.execute("SELECT after_state FROM sme_temporal_change WHERE agent_id=%s AND source_table=%s "
                "AND (JSON_UNQUOTE(JSON_EXTRACT(before_state,'$.effective_date'))=%s "
                "OR JSON_UNQUOTE(JSON_EXTRACT(after_state,'$.effective_date'))=%s) ORDER BY id DESC LIMIT 1",
                (row['agent_id'],DATED_MOVEMENTS,stamp,stamp))
    evidence = cur.fetchone()
    if evidence is None:
        return
    after = evidence[0]
    if isinstance(after,(str,bytes,bytearray)):
        after = json.loads(after)
    if not isinstance(after,dict) or _movement_evidence(after) != _movement_evidence(row):
        raise ValueError('Ce mouvement réel a été supprimé ou corrigé dans l’historique partagé. '
                         'Une archive mensuelle antérieure ne peut pas annuler cette correction. '
                         'Vérifiez explicitement une restauration complète si cette annulation est intentionnelle ; aucun mois n’a été modifié.')


def restore_month_backup(settings, archive, *, confirmed_month, _safety_backup=None):
    with operation_progress((('read', 'Vérification de la sauvegarde mensuelle'),
                             ('safety', 'Sauvegarde de sécurité'),
                             ('replace', 'Préparation du remplacement du mois'),
                             ('rows', 'Restauration des lignes et des références'),
                             ('verify', 'Contrôle des historiques et des autres mois'),
                             ('commit', 'Validation et finalisation'))) as operation:
        return _restore_month_backup_with_progress(settings, archive, confirmed_month=confirmed_month,
                                                  _safety_backup=_safety_backup, operation=operation)


def _restore_month_backup_with_progress(settings, archive, *, confirmed_month, _safety_backup, operation):
    operation.start('read')
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    if not session_has_admin_role() or not session_has_permission('system.restore'):
        raise PermissionError('Restauration mensuelle réservée aux administrateurs.')
    payload = inspect_month_backup(archive)
    month = payload['month']
    if confirmed_month != month: raise ValueError('Confirmez le mois exact de la sauvegarde.')
    from sme_gend.services.database_maintenance import centralized_maintenance
    from sme_gend.services.backup_restore_service import create_backup_to_default_dir, inspect_backup
    from sme_gend.services.mysql_import_period import purge_sme_periode_for_reimport
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.services.data_sync import publish_data_change
    from sme_gend.services.liste_generale_cache import invalidate_liste_cache
    with centralized_maintenance(settings, reason='Restauration du mois '+month):
        with MySQLConnection(settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                schema = metadata(cur)
                rows = validate_rows(payload, schema)
                if DATED_MOVEMENTS in rows:
                    from sme_gend.services.schema_migrations import require_capability
                    require_capability(settings,'dated_movements')
        operation.finish('read', detail=f'Mois {month} ; {sum(len(values) for values in rows.values()):,} ligne(s) à traiter')
        operation.start('safety')
        with operation.subtask('safety', current=0, total=2, detail='Préparation de la sauvegarde de sécurité'):
            backup = Path(_safety_backup or create_backup_to_default_dir(settings))
        with operation.subtask('safety', current=1, total=2, detail='Vérification de la sauvegarde de sécurité'):
            inspect_backup(backup)
        operation.finish('safety', detail=f'Sauvegarde vérifiée : {backup.name}')
        operation.start('replace', detail=f'Remplacement du mois {month} dans une transaction')
        try:
            with mysql_import_transaction(settings):
                with MySQLConnection(settings).connect() as conn:
                    with conn.cursor() as cur:
                        other_before = other_month_displays(cur, month, settings=settings)
                        ids = month_period_ids(cur, month, lock=True)
                        if ids:
                            target_period = ids[0]
                        else:
                            period = dict(rows['sme_periode'][0]); period.pop('id')
                            cur.execute("SELECT COUNT(*) FROM sme_periode WHERE LOWER(statut) IN ('ouvert','open')")
                            if cur.fetchone()[0]: period['statut']='Ferm\u00e9'
                            cols=list(period)
                            cur.execute('INSERT INTO sme_periode ('+','.join(map(quote,cols))+') VALUES ('+','.join(['%s']*len(cols))+')',tuple(period[c] for c in cols))
                            target_period = cur.lastrowid
                            ids=(target_period,)
                        from sme_gend.services.month_purge_plan import month_purge_plan
                        from sme_gend.services.automatic_position_links import external_position_links
                        position_dependencies = external_position_links(cur,
                            [item for pid in ids for item in month_purge_plan(pid, month,
                             imported_only=False, target_period_ids=ids)], month)
                purge_sme_periode_for_reimport(settings, month, imported_only=False,
                    preserve_manual_passations=True, _replace_position_links=True)
                with MySQLConnection(settings).connect() as conn:
                    with conn.cursor() as cur:
                        for pid in ids:
                            for table, meta in schema.items():
                                if 'import_lot_id' in meta['columns']:
                                    cur.execute(f'UPDATE {quote(table)} SET import_lot_id=NULL WHERE import_lot_id IN (SELECT id FROM import_lot WHERE sme_periode_id=%s)',(pid,))
                            cur.execute('DELETE e FROM etl_erreur e JOIN import_lot l ON l.id=e.import_lot_id WHERE l.sme_periode_id=%s',(pid,))
                            cur.execute('DELETE FROM import_lot WHERE sme_periode_id=%s',(pid,))
                        for table in EXTRA:
                            if table in schema:
                                cur.execute(f"DELETE FROM {quote(table)} WHERE TRIM(REPLACE(mois,'-',''))=%s",(month.replace('-',''),))
                        operation.finish('replace')
                        mapping = {}
                        restored_careers = []
                        new_dated_movements = []
                        checked_dated_agents = set()
                        def remember(table, original, target):
                            for col in schema[table]['columns']:
                                if original.get(col) is not None:
                                    mapping[(table,col,original[col])] = target[col]
                        for original in rows['sme_periode']:
                            cur.execute('SELECT * FROM sme_periode WHERE id=%s',(target_period,))
                            target=dict(zip([c[0] for c in cur.description],cur.fetchone()))
                            remember('sme_periode',original,target)
                        pending=[(table,r) for table,rs in rows.items() if table!='sme_periode' for r in rs]
                        total_rows = len(pending)
                        processed_rows = 0
                        operation.start('rows', total=total_rows)
                        inserted={}
                        while pending:
                            next_round=[]; progress=False
                            for table, original in pending:
                                meta=schema[table]; row=dict(original)
                                if any(row[col] is not None and (parent,parent_col,row[col]) not in mapping
                                       for col,parent,parent_col in meta['fk']):
                                    next_round.append((table,original));continue
                                for col,parent,parent_col in meta['fk']:
                                    if row[col] is not None: row[col]=mapping[(parent,parent_col,row[col])]
                                shared = table not in OWNED or (table=='agent_diplome' and original['id'] in payload.get('shared_diplomas', []))
                                existing = shared_match(cur,table,row,meta,schema) if shared else None
                                if table == DATED_MOVEMENTS and existing is not None and _movement_evidence(existing) != _movement_evidence(row):
                                    raise ValueError('Un mouvement réel à la même date contient des valeurs différentes. '
                                                     'Corrigez explicitement ce fait ; la restauration n’a rien remplacé.')
                                if table == 'agent_passation_grade':
                                    from sme_gend.services.grade_history_merge import matching_archive_passation
                                    existing = matching_archive_passation(cur, row['agent_id'], row['grade_libelle'],
                                        row['date_passation'], row.get('ndecision'))
                                if existing is None:
                                    if table == DATED_MOVEMENTS:
                                        _check_dated_correction_evidence(cur,row)
                                    if table=='carriere_situation':
                                        cur.execute("SELECT statut FROM sme_periode WHERE id=%s",(target_period,))
                                        row['est_actuelle']=int(str(cur.fetchone()[0] or '').casefold() in ('ouvert','open','r\u00e9ouvert','reouvert'))
                                    if table=='agent_position_event' and str(row.get('reference_decision') or '').startswith('sgi:auto:'):
                                        source_table,source_id,prefix,suffix=automatic_source(row['reference_decision'])
                                        key=(source_table,'id',source_id)
                                        if key not in mapping:
                                            if any(r['id']==source_id for r in rows.get(source_table, [])):
                                                next_round.append((table,original));continue
                                            descriptors=[d for d in payload.get('auto_sources',[]) if d['table']==source_table and d['row']['id']==source_id]
                                            if not descriptors:
                                                raise ValueError('Source automatique absente de la sauvegarde.')
                                            source={k:decode(v) for k,v in descriptors[0]['row'].items()}
                                            fields=source_match_fields(source)
                                            conditions=['agent_id=%s']+[quote(c)+' <=> %s' for c in fields]
                                            matches=read_rows(cur,source_table,' AND '.join(conditions),(row['agent_id'],*(source[c] for c in fields)))
                                            if len(matches)!=1:
                                                raise ValueError('Le stage ou evenement source appartient a un autre mois. Restaurez son mois en premier ; aucune modification enregistrée.')
                                            mapping[key]=matches[0]['id']
                                        row['reference_decision']=prefix+str(mapping[key])+suffix
                                    auto=meta['auto']
                                    if auto: row.pop(auto)
                                    cols=list(row)
                                    cur.execute('INSERT INTO '+quote(table)+' ('+','.join(map(quote,cols))+') VALUES ('+','.join(['%s']*len(cols))+')',tuple(row[c] for c in cols))
                                    if auto: row[auto]=cur.lastrowid
                                    existing=row
                                    inserted[table]=inserted.get(table,0)+1
                                    if table == DATED_MOVEMENTS:
                                        new_dated_movements.append(dict(row))
                                remember(table,original,existing)
                                if table == 'agent' and original['id'] in payload.get('dated_movement_agents', []):
                                    _check_dated_timeline(cur,month,rows,original['id'],existing['id'])
                                    checked_dated_agents.add(existing['id'])
                                if table == 'carriere_situation':
                                    restored_careers.append((existing['id'], existing['agent_id']))
                                progress=True
                                processed_rows += 1
                                if processed_rows % 100 == 0 or processed_rows == total_rows:
                                    operation.update('rows', current=processed_rows, total=total_rows,
                                        detail=f'{processed_rows:,}/{total_rows:,} ligne(s) traitée(s) ; {table}')
                            if not progress: raise ValueError('Dépendances circulaires ou références automatiques incomplètes. Restauration annulée.')
                            pending=next_round
                        operation.finish('rows', detail=f'{processed_rows:,} ligne(s) traitée(s)')
                        operation.start('verify', detail='Références de positions, grades et appartenance mensuelle')
                        from sme_gend.services.automatic_position_links import remap_external_position_links
                        remap_external_position_links(cur, position_dependencies)
                        from sme_gend.services.grade_history_merge import reconcile_restored_grades
                        grade_corrections = reconcile_restored_grades(settings, restored_careers, month)
                        if DATED_MOVEMENTS in rows:
                            from sme_gend.services.schema_migrations import require_capability
                            from sme_gend.services.membership_projection import apply_membership_for_agent
                            from sme_gend.services.temporal_changes import record_source_change
                            from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
                            require_capability(settings,'dated_movements')
                            cur.execute('SELECT DISTINCT agent_id FROM agent_effectif_movement WHERE effective_date<=%s',
                                        (last_day_of_month(month),))
                            if {int(value[0]) for value in cur.fetchall()} - checked_dated_agents:
                                raise ValueError('La cible possède des mouvements réels pour des agents absents de la sauvegarde. '
                                                 'Vérifiez explicitement ces faits avant de restaurer ce mois.')
                            expected_membership = {(row['agent_id'],row['sme_periode_id']):
                                (row['is_member'],row['baseline_member'],row['event_id'])
                                for row in read_rows(cur,MEMBERSHIP,'sme_periode_id=%s',(target_period,))}
                            bounds = {aid:first_day_of_month(month) for aid in checked_dated_agents}
                            from sme_gend.services.access_date_parse import parse_access_date
                            for row in new_dated_movements:
                                stamp = parse_access_date(row['effective_date'])
                                bounds[row['agent_id']] = min(bounds.get(row['agent_id'],stamp),stamp)
                                record_source_change(settings,row['agent_id'],DATED_MOVEMENTS,row['id'],None,row,
                                    reason='Restauration du mois '+month,effective_from=stamp,enabled=True)
                            for aid, since in sorted(bounds.items()):
                                apply_membership_for_agent(settings,aid,since)
                            actual_membership = {(row['agent_id'],row['sme_periode_id']):
                                (row['is_member'],row['baseline_member'],row['event_id'])
                                for row in read_rows(cur,MEMBERSHIP,'sme_periode_id=%s',(target_period,))}
                            if expected_membership != actual_membership:
                                raise ValueError('Les mouvements réels ne reproduisent pas la visibilité mensuelle sauvegardée. '
                                                 'Restauration annulée ; vérifiez les mouvements et leur observation mensuelle de référence.')
                        elif DATED_MOVEMENTS in schema and MEMBERSHIP in schema:
                            # Legacy bundles contain monthly observations, never real
                            # movement dates. Keep today's durable facts and reapply
                            # them over the restored observation rather than erase them.
                            from sme_gend.services.membership_scope import membership_projection_enabled
                            if membership_projection_enabled(settings,cursor=cur):
                                from sme_gend.services.membership_projection import apply_membership_for_agent
                                from sme_gend.services.mysql_import_period import first_day_of_month,last_day_of_month
                                cur.execute('SELECT DISTINCT agent_id FROM agent_effectif_movement WHERE effective_date<=%s',
                                            (last_day_of_month(month),))
                                for (aid,) in cur.fetchall():
                                    apply_membership_for_agent(settings,aid,first_day_of_month(month))
                        if other_month_displays(cur, month, settings=settings) != other_before:
                            raise ValueError('La restauration modifierait des données partagees visibles dans un autre mois. Operation annulée ; aucune modification enregistrée.')
                operation.finish('verify', detail='Autres mois et données partagées préservés')
                operation.start('commit', detail='Enregistrement de la révision, de l’audit et validation de la transaction')
                repo=AppConfigRepository(settings)
                publish_data_change(settings,reason='month_restored',mois=month)
                repo.append_audit(action='sme_month_restored',module='system',actor=resolve_audit_actor(),
                    sme_mois_context=month,payload={'backup':str(backup),'inserted':inserted,
                        'grade_history_reapplied': grade_corrections})
        finally:
            invalidate_liste_cache(None)
    operation.finish('commit', detail=f'Mois {month} restauré')
    return {'month':month,'backup':str(backup),'inserted':inserted,'grade_history_reapplied':len(grade_corrections)}


def other_month_displays(cur, selected, *, settings=None):
    from sme_gend.repositories.mysql_agent_repository import _select_liste
    cur.execute("SELECT DISTINCT annee_mois FROM sme_periode")
    months={normalize_import_mois(r[0]) for r in cur.fetchall()} - {selected}
    result={}
    for month in months:
        sql,args=_select_liste(month, settings=settings, cursor=cur)
        cur.execute(sql,args)
        result[month]=sorted(cur.fetchall(),key=lambda r:r[0])
    return result
