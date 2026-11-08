"""Targeted materialized months, derived from source histories and observed baselines."""
from dataclasses import dataclass
from datetime import date

from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month


@dataclass(frozen=True)
class MonthProjectionResult:
    month: str
    period_id: int
    created_rows: int
    projected_agents: int
    source_revision: str
    changed_rows: int = 0


COMPONENT_COLUMNS = {
    'position': ('position_admin_libelle', 'position_admin_id'),
    'grade': ('grade_libelle', 'grade_id', 'date_grade', 'ndecision'),
    'diplome': ('diplome_libelle', 'date_obtention'),
    'affectation': ('structure_code', 'structure_id', 'fonction_libelle', 'fonction_id',
                    'subdivision_arme_libelle', 'subdivision_arme_id'),
}


def _month(value):
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    result = canonical_sme_mois_yyyy_mm(str(value or ''))
    if not result:
        raise ValueError('Mois de projection invalide.')
    first_day_of_month(result)
    return result


def _records(cur, sql, params=()):
    cur.execute(sql, params)
    columns = [column[0] for column in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _project_rows(settings, rows, components_by_agent, *, after=None, effective_bounds=None):
    """Load each history family in batches, then apply changed fields in CASE batches."""
    if not rows:
        return 0
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.services.mysql_ref_etl import load_ref_cache_from_db, load_subdiv_flat_rows, prepare_carriere_write_fields
    wanted = set().union(*(components_by_agent.values()))
    histories = {component: {} for component in wanted}
    specs = {'position': ('agent_position_event', 'date_debut'),
             'grade': ('agent_passation_grade', 'date_passation'),
             'diplome': ('agent_diplome', 'date_obtention'),
             'affectation': ('agent_affectation', 'date_affectation')}
    aids = sorted(components_by_agent)
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cache, flat = load_ref_cache_from_db(cur), load_subdiv_flat_rows(cur)
            for component in wanted.intersection(specs):
                table, stamp = specs[component]
                for offset in range(0, len(aids), 500):
                    chunk = aids[offset:offset + 500]
                    found = _records(cur, f'SELECT * FROM {table} WHERE agent_id IN (' +
                                     ','.join(['%s'] * len(chunk)) + f') ORDER BY {stamp} DESC,id DESC', tuple(chunk))
                    for event in found:
                        effective = parse_access_date(event[stamp])
                        if effective is None or (after is not None and effective <= after):
                            continue  # An unknown Access date is not an inferred transition.
                        histories[component].setdefault(int(event['agent_id']), []).append((effective, event))
            changes = []
            for row in rows:
                aid = int(row['agent_id'])
                cutoff = min(last_day_of_month(_month(row['annee_mois'])),
                             parse_access_date(row.get('date_fin')) or date.max)
                fields = {}
                stub_values = {name: None for name in ListeGeneraleRow.__dataclass_fields__}
                stub_values.update(id=str(row['id']), grade=row.get('grade_libelle'),
                    position_admin=row.get('position_admin_libelle'), structure=row.get('structure_code'),
                    fonction=row.get('fonction_libelle'), subdivision_arme=row.get('subdivision_arme_libelle'))
                stub = ListeGeneraleRow(**stub_values)
                for component in components_by_agent.get(aid, ()):
                    if effective_bounds is not None and cutoff < effective_bounds[(aid, component)]:
                        continue
                    candidates = histories.get(component, {}).get(aid, ())
                    selected = next((event for stamp, event in candidates if stamp <= cutoff and
                        (component != 'position' or parse_access_date(event.get('date_fin')) is None or
                         parse_access_date(event['date_fin']) >= cutoff) and
                        (component != 'diplome' or event.get('categorie') == 'MILITAIRE')), None)
                    if selected is None:
                        continue  # Preserve the observed baseline when no dated change applies.
                    if component == 'position':
                        stub.position_admin = selected['position_admin']
                    elif component == 'grade':
                        stub.grade = selected['grade_libelle']
                        fields.update(date_grade=selected['date_passation'], ndecision=selected.get('ndecision'))
                    elif component == 'diplome':
                        fields.update(diplome_libelle=selected.get('libelle_libre'), date_obtention=selected['date_obtention'])
                    elif component == 'affectation':
                        stub.structure = selected['structure_code']
                        stub.fonction = selected.get('fonction_libelle')
                        stub.subdivision_arme = selected.get('subdivision_arme_libelle')
                    if component != 'diplome':
                        resolved = prepare_carriere_write_fields(cur, cache, stub, flat_rows=flat, auto_create=False)
                        fields.update({column: resolved[column] for column in COMPONENT_COLUMNS[component] if column in resolved})
                changed = {column: value for column, value in fields.items() if row.get(column) != value}
                if changed:
                    changes.append((int(row['id']), changed))
            for offset in range(0, len(changes), 200):
                batch = changes[offset:offset + 200]
                columns = sorted(set().union(*(set(values) for _, values in batch)))
                assignments, params = [], []
                for column in columns:
                    cases = []
                    for cid, values in batch:
                        if column in values:
                            cases.append('WHEN %s THEN %s')
                            params.extend((cid, values[column]))
                    assignments.append(column + '=CASE id ' + ' '.join(cases) + ' ELSE ' + column + ' END')
                ids = [cid for cid, _ in batch]
                cur.execute('UPDATE carriere_situation SET ' + ','.join(assignments) + ' WHERE id IN (' +
                            ','.join(['%s'] * len(ids)) + ')', (*params, *ids))
    return len(changes)


def refresh_month_projection(settings, month):
    """Drain known invalidations touching this month; never replay inferred Access dates."""
    from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
    from sme_gend.services.schema_migrations import require_capability
    from sme_gend.services.temporal_changes import acknowledge_projection
    assert_exclusive_publication_barrier(settings)
    require_capability(settings, 'temporal_projection')
    month = _month(month)
    from sme_gend.services.membership_projection import refresh_membership_projection
    refresh_membership_projection(settings, month)
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            from sme_gend.services.month_period_scope import month_period_ids
            period_ids = month_period_ids(cur, month)
            if not period_ids:
                return {'month': month, 'projected_agents': 0, 'changed_rows': 0}
            pending = _records(cur, "SELECT d.* FROM sme_projection_dirty d WHERE d.state='pending' "
                'AND d.effective_from<=%s AND EXISTS (SELECT 1 FROM carriere_situation c JOIN sme_periode p '
                'ON p.id=c.sme_periode_id WHERE c.agent_id=d.agent_id AND p.id IN (' +
                ','.join(['%s'] * len(period_ids)) + ')) FOR UPDATE',
                (last_day_of_month(month), *period_ids))
            if not pending:
                return {'month': month, 'projected_agents': 0, 'changed_rows': 0}
            membership_pending = [row for row in pending if row['component'] == 'membership']
            pending = [row for row in pending if row['component'] != 'membership']
            if not pending:
                return {'month': month, 'projected_agents': 0, 'changed_rows': 0,
                        'pending_membership': membership_pending}
            components = {}
            earliest = min(parse_access_date(row['effective_from']) for row in pending)
            for row in pending:
                components.setdefault(int(row['agent_id']), set()).add(row['component'])
            bounds = {(int(row['agent_id']), row['component']): parse_access_date(row['effective_from']) for row in pending}
            aids = sorted(components)
            rows = []
            for offset in range(0, len(aids), 500):
                chunk = aids[offset:offset + 500]
                rows.extend(_records(cur, 'SELECT c.*,p.annee_mois FROM carriere_situation c JOIN sme_periode p '
                    'ON p.id=c.sme_periode_id WHERE c.agent_id IN (' +
                    ','.join(['%s'] * len(chunk)) + ')', tuple(chunk)))
            rows = [row for row in rows if _month(row['annee_mois']) >= earliest.strftime('%Y-%m')]
    changed = _project_rows(settings, rows, components, effective_bounds=bounds)
    for row in pending:
        acknowledge_projection(settings, row['agent_id'], row['component'], row['requested_token'])
    return {'month': month, 'projected_agents': len(components), 'changed_rows': changed,
            'pending_membership': membership_pending}


def prepare_month_projection(settings, target_month, source_month, *, expected_source_revision=None):
    from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
    from sme_gend.services.schema_migrations import require_capability
    from sme_gend.services.active_month_guard import require_period_scope
    from sme_gend.services.workflow_permissions import session_can_perform_action
    from sme_gend.services.data_sync import read_data_revision
    from sme_gend.services.mysql_import_period import ensure_sme_periode
    from sme_gend.services.month_period_scope import month_period_ids
    from sme_gend.services.business_audit import record_business_action
    assert_exclusive_publication_barrier(settings)
    require_period_scope(settings)
    require_capability(settings, 'temporal_projection')
    if not (session_can_perform_action('workflow.set_active_month') or session_can_perform_action('workflow.close_month')):
        raise PermissionError('Permission de préparation du mois refusée.')
    target, source = _month(target_month), _month(source_month)
    if first_day_of_month(target) <= last_day_of_month(source):
        raise ValueError('Le mois cible doit suivre le mois source.')
    revision = read_data_revision(settings)
    if expected_source_revision is not None and revision != expected_source_revision:
        raise ValueError('Les données source ont changé. Rechargez avant de préparer le mois.')
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            source_ids = month_period_ids(cur, source, lock=True)
            if len(source_ids) != 1:
                raise ValueError('Le mois source est absent ou ambigu.')
            source_id = int(source_ids[0])
            target_ids = month_period_ids(cur, target, lock=True)
            targets = []
            if target_ids:
                cur.execute('SELECT id,statut FROM sme_periode WHERE id IN (' +
                            ','.join(['%s'] * len(target_ids)) + ') FOR UPDATE', tuple(target_ids))
                targets = cur.fetchall()
            if len(targets) > 1:
                raise ValueError('Le mois cible est ambigu.')
            if targets:
                pid, status = targets[0]
                if str(status or '').strip().casefold() not in ('préparé', 'prepare', 'prepared'):
                    raise ValueError('La projection ne remplace pas un mois existant ou importé.')
                cur.execute('SELECT (SELECT COUNT(*) FROM carriere_situation WHERE sme_periode_id=%s)+'
                            '(SELECT COUNT(*) FROM import_lot WHERE sme_periode_id=%s)', (pid, pid))
                if int(cur.fetchone()[0]):
                    raise ValueError('Le mois cible contient déjà des données ; aucune projection remplacée.')
            else:
                pid = ensure_sme_periode(conn, target, statut='Préparé')
            cur.execute('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                        "AND TABLE_NAME='carriere_situation' ORDER BY ORDINAL_POSITION")
            columns = [str(row[0]) for row in cur.fetchall() if row[0] not in
                       {'id', 'created_at', 'updated_at', 'sme_periode_id', 'date_debut', 'date_fin', 'est_actuelle', 'import_lot_id'}]
            quoted = ','.join('`' + column + '`' for column in columns)
            cur.execute('INSERT INTO carriere_situation (' + quoted + ',sme_periode_id,date_debut,date_fin,est_actuelle,import_lot_id) '
                        'SELECT ' + ','.join('c.`' + column + '`' for column in columns) + ',%s,%s,%s,0,NULL '
                        'FROM carriere_situation c WHERE c.sme_periode_id=%s AND c.id=(SELECT MAX(pick.id) '
                        'FROM carriere_situation pick WHERE pick.agent_id=c.agent_id AND pick.sme_periode_id=%s) '
                        'AND (c.date_fin IS NULL OR c.date_fin>=%s)',
                        (pid, first_day_of_month(target), last_day_of_month(target), source_id, source_id, last_day_of_month(source)))
            created = int(cur.rowcount)
            cur.execute('SELECT DISTINCT agent_id FROM carriere_situation WHERE sme_periode_id=%s', (source_id,))
            source_agents = {int(row[0]) for row in cur.fetchall()}
            from sme_gend.services.membership_projection import refresh_membership_projection
            refresh_membership_projection(settings, target, source_period_id=source_id, target_period_id=pid)
            rows = _records(cur, 'SELECT c.*,p.annee_mois FROM carriere_situation c JOIN sme_periode p '
                            'ON p.id=c.sme_periode_id WHERE c.sme_periode_id=%s', (pid,))
            created = len(rows)
    components = {int(row['agent_id']): set(COMPONENT_COLUMNS) for row in rows}
    changed = _project_rows(settings, [row for row in rows if int(row['agent_id']) in source_agents],
                            components, after=last_day_of_month(source))
    changed += _project_rows(settings, [row for row in rows if int(row['agent_id']) not in source_agents], components)
    record_business_action(settings, action='month_projection_prepared', module='workflow', entity_id=pid,
        after={'source_month': source, 'target_month': target, 'created_rows': created,
               'projected_agents': len(components), 'changed_rows': changed, 'source_revision': revision}, mois=target)
    return MonthProjectionResult(target, int(pid), created, len(components), revision, changed)
