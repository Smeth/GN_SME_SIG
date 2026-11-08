"""Administrative corrections of actual returns, with CAS and immutable evidence."""
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import json

from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.activity_completion import RETURN_TABLES, activity_return_reference


@dataclass(frozen=True)
class ActivityReturnSnapshot:
    agent_id: int
    source_table: str
    source_id: int
    start_date: date
    return_date: date
    position_label: str
    agent_name: str
    reference: str | None
    revision: str = field(repr=False)


def _authorize(settings):
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    if not session_has_admin_role() or not session_has_permission('effectifs.history_correction'):
        raise PermissionError('La correction d’un retour validé exige un administrateur autorisé.')
    from sme_gend.services.schema_migrations import require_capability
    require_capability(settings, 'temporal_projection')


def _read(cur, agent_id, table, source_id, *, lock=False):
    if table not in RETURN_TABLES:
        raise ValueError('Type d’activité non autorisé pour une correction de retour.')
    cur.execute(f'SELECT * FROM {table} WHERE id=%s AND agent_id=%s' + (' FOR UPDATE' if lock else ''),
                (int(source_id), int(agent_id)))
    fields = [column[0] for column in cur.description]
    value = cur.fetchone()
    if value is None:
        raise ValueError('Activité introuvable pour cet agent.')
    source = dict(zip(fields, value))
    start = parse_access_date(source.get('date_desertion' if table == 'agent_desertion' else 'date_debut'))
    if start is None:
        raise ValueError('La date de début de cette activité doit être régularisée avant son retour.')
    base = f'sgi:auto:{table}:{int(source_id)}'
    reference = activity_return_reference(table, source_id)
    params = [int(agent_id), base, reference]
    where = '(reference_decision=%s OR reference_decision=%s'
    if table == 'parcours_stage' and source.get('date_fin'):
        code = source.get('type_stage_code')
        if code not in ('LOCAL', 'INTERNE', 'EXTERIEUR'):
            raise ValueError('Type de stage non reconnu.')
        old_end = parse_access_date(source['date_fin'])
        legacy = f'sgi:auto:stage_end:%:{code}:{old_end.isoformat()}'
        cur.execute('SELECT COUNT(*) FROM parcours_stage WHERE agent_id=%s AND type_stage_code=%s AND date_fin=%s',
                    (int(agent_id), code, old_end))
        unique_legacy_stage = int(cur.fetchone()[0]) == 1
        if unique_legacy_stage:
            where += ' OR reference_decision LIKE %s'
            params.append(legacy)
    where += ')'
    cur.execute('SELECT * FROM agent_position_event WHERE agent_id=%s AND ' + where + ' ORDER BY id' +
                (' FOR UPDATE' if lock else ''), tuple(params))
    columns = [column[0] for column in cur.description]
    markers = [dict(zip(columns, row)) for row in cur.fetchall()]
    returns = [row for row in markers if row['reference_decision'] != base and row['position_admin'] == 'Présent']
    dates = {parse_access_date(row['date_debut']) for row in returns}
    if len(dates) != 1:
        raise ValueError('Aucun retour validé unique n’est rattaché à cette fiche ; régularisez son historique.')
    actual = dates.pop()
    if table != 'agent_desertion' and parse_access_date(source.get('date_fin')) != actual:
        raise ValueError('La fin de fiche et son retour validé sont incohérents ; faites vérifier cet historique.')
    cur.execute('SELECT noms FROM agent WHERE id=%s', (int(agent_id),))
    agent = cur.fetchone()
    from sme_gend.services.business_audit import audit_values
    revision = sha256(json.dumps(audit_values([source, markers]), sort_keys=True,
                                ensure_ascii=False, default=str).encode('utf-8')).hexdigest()
    note = None
    for row in returns:
        try:
            payload = json.loads(row.get('observation') or '{}')
            note = payload.get('return_reference') if isinstance(payload, dict) else None
        except (TypeError, ValueError):
            pass
    from sme_gend.services.temporal_events import _event_position
    snapshot = ActivityReturnSnapshot(int(agent_id), table, int(source_id), start, actual,
        _event_position(table, source), str(agent[0] or '') if agent else '', note, revision)
    return snapshot, source, markers, returns


def load_activity_return(settings, agent_id, source_table, source_id):
    _authorize(settings)
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_read_snapshot, has_import_transaction
    from contextlib import nullcontext
    with nullcontext() if has_import_transaction(settings) else mysql_read_snapshot(settings):
        with MySQLConnection(settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                return _read(cur, agent_id, source_table, source_id)[0]


def list_activity_returns(settings, agent_id):
    _authorize(settings)
    from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_read_snapshot
    from sme_gend.services.automatic_position_links import automatic_source
    sources = set()
    result = []
    with mysql_read_snapshot(settings):
        with MySQLConnection(settings).connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT reference_decision FROM agent_position_event WHERE agent_id=%s '
                            "AND (reference_decision LIKE 'sgi:auto:activity_return:%%' OR reference_decision LIKE 'sgi:auto:stage_end:%%')",
                            (int(agent_id),))
                references = [row[0] for row in cur.fetchall()]
                for reference in references:
                    parts = reference.split(':')
                    if parts[2] == 'activity_return':
                        table, source_id, _, _ = automatic_source(reference)
                        sources.add((table, source_id))
                    else:
                        cur.execute('SELECT id FROM parcours_stage WHERE agent_id=%s AND type_stage_code=%s AND date_fin=%s',
                                    (int(agent_id), parts[4], parse_access_date(parts[5])))
                        ids = cur.fetchall()
                        if len(ids) == 1:
                            sources.add(('parcours_stage', int(ids[0][0])))
                for table, source_id in sorted(sources):
                    result.append(_read(cur, agent_id, table, source_id)[0])
    return sorted(result, key=lambda value: (value.return_date, value.source_id), reverse=True)


def correct_activity_return(settings, snapshot, actual_return_date, reason, *, reference=None):
    _authorize(settings)
    if not isinstance(snapshot, ActivityReturnSnapshot):
        raise ValueError('Rechargez la version du retour avant de le corriger.')
    actual = parse_access_date(actual_return_date)
    if actual is None or actual > date.today() or actual < snapshot.start_date:
        raise ValueError('La date corrigée doit être valide, non future et postérieure ou égale au début de l’activité.')
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('Le motif de correction est obligatoire.')
    reference = str(reference or '').strip() or None
    if reference is not None and len(reference) > 256:
        raise ValueError('La Réf/note est limitée à 256 caractères.')
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.repositories.write_guard import lock_agent
    from sme_gend.services.temporal_events import record_position, project_positions
    from sme_gend.services.temporal_changes import record_source_change, acknowledge_projection
    from sme_gend.services.business_audit import record_business_action
    with _personnel_write(settings, reason='activity_return_corrected'):
        if lock_agent(settings, agent_id=snapshot.agent_id) is None:
            raise ValueError('Agent introuvable. Rechargez les données.')
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                current, source, markers, returns = _read(cur, snapshot.agent_id, snapshot.source_table,
                                                        snapshot.source_id, lock=True)
                if current.revision != snapshot.revision:
                    raise ValueError('Ce retour a été modifié depuis son affichage. Rechargez avant de recommencer.')
                changing_date = actual != current.return_date
                if not changing_date and reference == current.reference:
                    return current
                excluded = [row['id'] for row in markers]
                if actual > current.return_date:
                    cur.execute('SELECT id FROM agent_position_event WHERE agent_id=%s AND date_debut>%s '
                                'AND date_debut<=%s AND id NOT IN (' + ','.join(['%s'] * len(excluded)) + ') LIMIT 1 FOR UPDATE',
                                (snapshot.agent_id, current.return_date, actual, *excluded))
                    if cur.fetchone() is not None:
                        raise ValueError('Cette correction chevaucherait une autre position déjà enregistrée. '
                                         'La nouvelle activité a été conservée.')
                elif changing_date:
                    cur.execute('SELECT id FROM agent_position_event WHERE agent_id=%s AND date_debut<=%s '
                                'AND (date_fin IS NULL OR date_fin>=%s) '
                                "AND position_admin NOT IN ('Présent','Present') "
                                'AND id NOT IN (' + ','.join(['%s'] * len(excluded)) + ') LIMIT 1 FOR UPDATE',
                                (snapshot.agent_id, actual, actual, *excluded))
                    if cur.fetchone() is not None:
                        raise ValueError('Une autre activité est encore ouverte à cette date. Régularisez cet enchaînement '
                                         'avant de corriger le retour ; aucune position n’a été remplacée.')
                table = snapshot.source_table
                if changing_date and table != 'agent_desertion':
                    cur.execute(f'UPDATE {table} SET date_fin=%s WHERE id=%s AND agent_id=%s',
                                (actual, snapshot.source_id, snapshot.agent_id))
                marker = activity_return_reference(table, snapshot.source_id)
                if changing_date:
                    cur.execute('DELETE FROM agent_position_event WHERE id IN (' +
                                ','.join(['%s'] * len(returns)) + ') AND agent_id=%s',
                                (*(row['id'] for row in returns), snapshot.agent_id))
                    record_position(settings, snapshot.agent_id, current.position_label, current.start_date, actual,
                        reference=f'sgi:auto:{table}:{snapshot.source_id}', project=False, automatic_return=False)
                    record_position(settings, snapshot.agent_id, 'Présent', actual, reference=marker, project=False)
                reference_ids = [row['id'] for row in returns]
                cur.execute('UPDATE agent_position_event SET observation=%s WHERE agent_id=%s AND '
                            '(reference_decision=%s OR id IN (' + ','.join(['%s'] * len(reference_ids)) + '))',
                            (json.dumps({'return_reference': reference}, ensure_ascii=False), snapshot.agent_id, marker, *reference_ids))
                since = min(current.return_date, actual)
                if changing_date:
                    project_positions(settings, snapshot.agent_id, since)
                after = dict(source)
                if table != 'agent_desertion':
                    after['date_fin'] = actual
                before_evidence = {**source, 'date_retour': current.return_date, 'return_reference': current.reference}
                after_evidence = {**after, 'date_retour': actual, 'return_reference': reference}
                token = record_source_change(settings, snapshot.agent_id, table, snapshot.source_id,
                    before_evidence, after_evidence, mutation='correct_return', reason=reason, effective_from=since,
                    enabled=True)
                acknowledge_projection(settings, snapshot.agent_id, 'position', token)
                record_business_action(settings, action='activity_return_corrected', module='personnel',
                    entity_id=snapshot.source_id, before=before_evidence,
                    after={**after_evidence, 'reason': reason, 'agent_id': snapshot.agent_id},
                    personnel_id=str(snapshot.agent_id), personnel_nom=current.agent_name, mois=actual.strftime('%Y-%m'))
                return _read(cur, snapshot.agent_id, table, snapshot.source_id)[0]
