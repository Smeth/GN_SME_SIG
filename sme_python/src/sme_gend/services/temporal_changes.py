"""Append-only evidence and targeted invalidations beside existing source registers."""
from datetime import datetime, timezone
import json
from uuid import uuid4
from contextlib import contextmanager
from contextvars import ContextVar

from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.business_audit import audit_values

SOURCE_COMPONENTS = {
    'agent_mission': 'position', 'agent_conge': 'position', 'agent_desertion': 'position',
    'agent_malade': 'position', 'parcours_stage': 'position', 'agent_position_event': 'position',
    'agent_diplome': 'diplome', 'agent_passation_grade': 'grade', 'agent_affectation': 'affectation',
    'agent_distinction': 'distinction',
    'mouvement_effectif': 'membership',
    'agent_effectif_movement': 'membership',
}
_repository_scope = ContextVar('temporal_repository_journal', default=frozenset())


def temporal_capability_enabled(settings):
    """Read-only probe; legacy databases keep their existing central audit."""
    from sme_gend.repositories.mysql_connection import transaction_local_cache, mysql_database_key
    cache = transaction_local_cache('temporal_probe')
    key = mysql_database_key(settings)
    if cache is not None and key in cache:
        return cache[key]
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() '
                        "AND TABLE_NAME IN ('_sme_schema_migration','sme_temporal_change','sme_projection_dirty')")
            if int(cur.fetchone()[0]) != 3:
                if cache is not None:
                    cache[key] = False
                return False
            cur.execute("SELECT state FROM _sme_schema_migration WHERE version='0002'")
            row = cur.fetchone()
            enabled = row is not None and row[0] == 'applied'
            if cache is not None:
                cache[key] = enabled
            return enabled


def source_effective_date(source):
    if source is None:
        return None
    for field in ('effective_date', 'date_effet', 'date_debut', 'date_desertion', 'date_passation', 'date_affectation',
                  'date_obtention', 'date_attribution'):
        stamp = parse_access_date(source.get(field))
        if stamp is not None:
            return stamp
    return None


def queue_projection(settings, agent_id, component, effective_from):
    if component not in {'position', 'grade', 'diplome', 'affectation', 'distinction', 'membership'}:
        raise ValueError('Composante de projection non autorisée.')
    stamp = parse_access_date(effective_from)
    if stamp is None:
        # Unknown Access observations are not converted into invented dates.
        return None
    if not has_import_transaction(settings):
        raise RuntimeError('Invalidation temporelle sans transaction partagée.')
    token = uuid4().hex
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO sme_projection_dirty(agent_id,component,effective_from,requested_token,'
                        'requested_at,state) VALUES(%s,%s,%s,%s,%s,\'pending\') '
                        "ON DUPLICATE KEY UPDATE effective_from=IF(state='pending',LEAST(effective_from,VALUES(effective_from)),VALUES(effective_from)),"
                        'requested_token=VALUES(requested_token),requested_at=VALUES(requested_at),state=\'pending\',last_error=NULL',
                        (int(agent_id), component, stamp, token, datetime.now(timezone.utc).replace(tzinfo=None)))
    return token


def acknowledge_projection(settings, agent_id, component, token):
    if token is None:
        return
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_projection_dirty SET state='done',processed_token=%s,processed_at=%s,last_error=NULL "
                        'WHERE agent_id=%s AND component=%s AND requested_token=%s',
                        (token, datetime.now(timezone.utc).replace(tzinfo=None), int(agent_id), component, token))


@contextmanager
def journal_repository_mutation(settings, agent_id, method):
    families = {'diplome': 'agent_diplome', 'passation': 'agent_passation_grade',
                'affectation': 'agent_affectation', 'distinction': 'agent_distinction'}
    family = next((name for name in families if method.startswith(('insert_' + name, 'update_' + name,
                     'delete_' + name, 'record_list_saisie_' + name))), None)
    if family is None or agent_id is None:
        yield
        return
    table = families[family]
    key = (id(settings), int(agent_id), table)
    active = _repository_scope.get()
    if key in active or not temporal_capability_enabled(settings):
        yield
        return
    def read():
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM {table} WHERE agent_id=%s ORDER BY id FOR UPDATE', (int(agent_id),))
                fields = [column[0] for column in cur.description]
                return {int(row[0]): dict(zip(fields, row)) for row in cur.fetchall()}
    previous = read()
    marker = _repository_scope.set(active | {key})
    try:
        yield
        current = read()
        for source_id in sorted(previous.keys() | current.keys()):
            record_source_change(settings, agent_id, table, source_id, previous.get(source_id), current.get(source_id),
                                 reason=method, enabled=True)
    finally:
        _repository_scope.reset(marker)


def record_source_change(settings, agent_id, table, source_id, before, after, *, reason=None,
                         mutation=None, effective_from=None, enabled=None):
    if table not in SOURCE_COMPONENTS:
        raise ValueError('Source temporelle non autorisée.')
    if before == after:
        return None
    if enabled is None:
        enabled = temporal_capability_enabled(settings)
    if not enabled:
        return None
    if not has_import_transaction(settings):
        raise RuntimeError('Journal temporel sans transaction partagée.')
    from sme_gend.services.auth_context import resolve_write_actor
    dates = [source_effective_date(value) for value in (before, after)]
    dates = [value for value in dates if value is not None]
    stamp = parse_access_date(effective_from) or (min(dates) if dates else None)
    operation = mutation or ('create' if before is None else 'delete' if after is None else 'update')
    provenance = {key: (after or before or {}).get(key) for key in
                  ('sme_periode_id', 'import_lot_id', 'source_ligne_legacy', 'source_code')}
    payload = lambda value: json.dumps(audit_values(value), ensure_ascii=False, default=str) if value is not None else None
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('INSERT INTO sme_temporal_change(change_uid,agent_id,source_table,source_id,mutation,'
                        'effective_from,effective_until,recorded_at,actor,reason,before_state,after_state,provenance) '
                        'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (uuid4().hex, int(agent_id), table, int(source_id), operation, stamp,
                         parse_access_date((after or before or {}).get('date_fin')),
                         datetime.now(timezone.utc).replace(tzinfo=None), resolve_write_actor(None), reason,
                         payload(before), payload(after), payload(provenance)))
    return queue_projection(settings, agent_id, SOURCE_COMPONENTS[table], stamp)
