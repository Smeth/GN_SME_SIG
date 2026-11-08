"""Materialized workforce membership, preserving the original monthly observation.

Real dated facts override an observation only through an authorized correction.
Removing a fact restores that observation; career/provenance rows are never erased.
"""
from datetime import datetime, timezone

from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
from sme_gend.services.operation_progress import operation_progress


def _records(cursor, sql, params=()):
    cursor.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _current_career_flag(settings, month, status):
    if str(status or '').strip().casefold() not in {'ouvert','open','réouvert','reouvert','reopened'}:
        return 0
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    return int(load_active_month_snapshot(settings).active_month == month)


def _materialize_gain(cursor, aid, pid, month, careers, columns, current_flag):
    candidates = [(canonical_sme_mois_yyyy_mm(str(row['annee_mois'])), row) for row in careers]
    candidates = [(stamp, row) for stamp, row in candidates if stamp and stamp <= month]
    template = max(candidates, key=lambda pair: (pair[0], pair[1]['id']))[1] if candidates else {}
    values = {column: template.get(column) for column in columns if column in template}
    values.update(agent_id=aid, sme_periode_id=pid, date_debut=first_day_of_month(month),
                  date_fin=last_day_of_month(month), est_actuelle=current_flag, import_lot_id=None)
    cursor.execute('INSERT INTO carriere_situation (' + ','.join('`'+key+'`' for key in values) +
                   ') VALUES (' + ','.join(['%s'] * len(values)) + ')', tuple(values.values()))
    return {**values, 'id': cursor.lastrowid, 'annee_mois': month}


def apply_membership_for_agent(settings, agent_id, since, *, _period_ids=None):
    """Caller holds the agent lock and a shared business transaction (or X gate)."""
    with operation_progress((('history', 'Lecture des faits et situations de l’agent'),
                             ('periods', 'Recalcul de l’effectif par période'),
                             ('acknowledge', 'Mise à jour du suivi des projections'))) as progress:
        progress.start('history', detail='Lecture des périodes, mouvements et situations de l’agent…')
        return _apply_membership_for_agent(settings, agent_id, since, _period_ids=_period_ids, progress=progress)


def _apply_membership_for_agent(settings, agent_id, since, *, _period_ids, progress):
    if not has_import_transaction(settings):
        raise RuntimeError('La projection des effectifs exige la transaction du mouvement.')
    since = parse_access_date(since)
    if since is None:
        raise ValueError('La date réelle du mouvement est obligatoire.')
    aid = int(agent_id)
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            # A row lock serializes this agent without serializing other agents.
            cursor.execute('SELECT id FROM agent WHERE id=%s FOR UPDATE', (aid,))
            if cursor.fetchone() is None:
                raise ValueError('Agent introuvable pour la projection des effectifs.')
            cursor.execute('SELECT id,annee_mois,statut FROM sme_periode ORDER BY id')
            raw_periods = cursor.fetchall()
            statuses = {int(pid):status for pid,month,status in raw_periods}
            periods = [(int(pid), canonical_sme_mois_yyyy_mm(str(month))) for pid, month, status in raw_periods]
            periods = [(pid, month) for pid, month in periods if month and last_day_of_month(month) >= since]
            if _period_ids is not None:
                periods = [(pid, month) for pid, month in periods if pid in _period_ids]
            events = _records(cursor, 'SELECT id,category,effective_date FROM agent_effectif_movement '
                              'WHERE agent_id=%s ORDER BY effective_date DESC,id DESC', (aid,))
            careers = _records(cursor, 'SELECT c.*,p.annee_mois FROM carriere_situation c JOIN sme_periode p '
                               'ON p.id=c.sme_periode_id WHERE c.agent_id=%s', (aid,))
            current = {int(row['sme_periode_id']): row for row in careers}
            maps = _records(cursor, 'SELECT * FROM sme_month_membership WHERE agent_id=%s FOR UPDATE', (aid,))
            maps = {int(row['sme_periode_id']): row for row in maps}
            columns = None
            progress.finish('history', detail=f'{len(periods)} période(s) concernée(s) par les faits datés.')
            progress.start('periods', total=len(periods), detail='Recalcul des périodes mensuelles concernées…')
            for index, (pid, month) in enumerate(periods, 1):
                event = next((row for row in events if row['effective_date'] <= last_day_of_month(month)), None)
                baseline = int(maps[pid]['baseline_member']) if pid in maps else int(pid in current)
                is_member = int(event['category'] == 'GAIN') if event else baseline
                if is_member and pid not in current:
                    if columns is None:
                        cursor.execute('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                                       "AND TABLE_NAME='carriere_situation' ORDER BY ORDINAL_POSITION")
                        columns = [row[0] for row in cursor.fetchall() if row[0] not in
                                   {'id', 'cree_le', 'updated_le', 'created_at', 'updated_at'}]
                    career = _materialize_gain(cursor, aid, pid, month, careers, columns,
                                               _current_career_flag(settings,month,statuses[pid]))
                    current[pid] = career
                    careers.append(career)
                if event is None and pid not in maps:
                    if index % 25 == 0 or index == len(periods):
                        progress.update('periods', current=index, total=len(periods),
                                        detail=f'{index}/{len(periods)} période(s) contrôlée(s), jusqu’à {month}.')
                    continue
                cursor.execute('INSERT INTO sme_month_membership(agent_id,sme_periode_id,is_member,baseline_member,'
                    'event_id,updated_at) VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE '
                    'is_member=VALUES(is_member),event_id=VALUES(event_id),updated_at=VALUES(updated_at)',
                    (aid, pid, is_member, baseline, event['id'] if event else None,
                     datetime.now(timezone.utc).replace(tzinfo=None)))
                if index % 25 == 0 or index == len(periods):
                    progress.update('periods', current=index, total=len(periods),
                                    detail=f'{index}/{len(periods)} période(s) recalculée(s), jusqu’à {month}.')
            progress.finish('periods', detail=f'{len(periods)} période(s) examinée(s).')
            progress.start('acknowledge', detail='Contrôle de la demande de recalcul en attente…')
            cursor.execute("SELECT requested_token FROM sme_projection_dirty WHERE agent_id=%s "
                           "AND component='membership' AND state='pending' FOR UPDATE", (aid,))
            pending = cursor.fetchone()
    if pending and _period_ids is None:
        from sme_gend.services.temporal_changes import acknowledge_projection
        acknowledge_projection(settings, aid, 'membership', pending[0])
    progress.finish('acknowledge', detail='Suivi de projection actualisé dans la transaction du mouvement.')


def _rebuild_target_membership(settings, cursor, month, pid):
    """Batch a new/fully replaced observation; do not replay every historical month."""
    cursor.execute('INSERT INTO sme_month_membership(agent_id,sme_periode_id,is_member,baseline_member,event_id,updated_at) '
        'SELECT e.agent_id,%s,e.category=\'GAIN\',COALESCE(m.baseline_member,IF(c.id IS NULL,0,1)),e.id,%s '
        'FROM agent_effectif_movement e '
        'LEFT JOIN sme_month_membership m ON m.agent_id=e.agent_id AND m.sme_periode_id=%s '
        'LEFT JOIN carriere_situation c ON c.agent_id=e.agent_id AND c.sme_periode_id=%s '
        'WHERE e.effective_date<=%s AND NOT EXISTS (SELECT 1 FROM agent_effectif_movement later '
        'WHERE later.agent_id=e.agent_id AND later.effective_date<=%s AND later.effective_date>e.effective_date) '
        'ON DUPLICATE KEY UPDATE is_member=VALUES(is_member),event_id=VALUES(event_id),updated_at=VALUES(updated_at)',
        (int(pid),datetime.now(timezone.utc).replace(tzinfo=None),int(pid),int(pid),
         last_day_of_month(month),last_day_of_month(month)))
    cursor.execute('SELECT m.agent_id FROM sme_month_membership m LEFT JOIN carriere_situation c '
                   'ON c.agent_id=m.agent_id AND c.sme_periode_id=m.sme_periode_id '
                   'WHERE m.sme_periode_id=%s AND m.is_member=1 AND c.id IS NULL ORDER BY m.agent_id', (int(pid),))
    missing = [int(row[0]) for row in cursor.fetchall()]
    if not missing:
        return
    cursor.execute('SELECT statut FROM sme_periode WHERE id=%s',(int(pid),))
    current_flag = _current_career_flag(settings,month,cursor.fetchone()[0])
    cursor.execute('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() '
                   "AND TABLE_NAME='carriere_situation' ORDER BY ORDINAL_POSITION")
    columns = [row[0] for row in cursor.fetchall() if row[0] not in
               {'id','cree_le','updated_le','created_at','updated_at'}]
    insert = 'INSERT INTO carriere_situation ('+','.join('`'+key+'`' for key in columns)+') VALUES ('+','.join(['%s']*len(columns))+')'
    for offset in range(0,len(missing),500):
        batch = missing[offset:offset+500]
        histories = _records(cursor,'SELECT c.*,p.annee_mois FROM carriere_situation c JOIN sme_periode p '
                              'ON p.id=c.sme_periode_id WHERE c.agent_id IN ('+','.join(['%s']*len(batch))+')',tuple(batch))
        templates = {}
        for row in histories:
            stamp = canonical_sme_mois_yyyy_mm(str(row['annee_mois']))
            if stamp and stamp<=month:
                rank = stamp,int(row['id'])
                if row['agent_id'] not in templates or rank>templates[row['agent_id']][0]:
                    templates[row['agent_id']] = rank,row
        values = []
        for aid in batch:
            record = {**templates.get(aid,(None,{}))[1], 'agent_id':aid,'sme_periode_id':int(pid),
                      'date_debut':first_day_of_month(month),'date_fin':last_day_of_month(month),
                      'est_actuelle':current_flag,'import_lot_id':None}
            values.append(tuple(record.get(column) for column in columns))
        cursor.executemany(insert,values)


def refresh_membership_projection(settings, month, *, source_period_id=None, target_period_id=None):
    from sme_gend.services.membership_scope import membership_projection_enabled
    from sme_gend.services.publication_write_barrier import assert_exclusive_publication_barrier
    assert_exclusive_publication_barrier(settings)
    if not membership_projection_enabled(settings):
        return
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            if source_period_id is not None:
                # Carry the original baseline too, so deleting/correcting a dated fact
                # can undo its projection in all prepared months without data loss.
                cursor.execute('INSERT INTO sme_month_membership(agent_id,sme_periode_id,is_member,baseline_member,'
                    'event_id,updated_at) SELECT m.agent_id,%s,m.is_member,m.baseline_member,m.event_id,%s '
                    'FROM sme_month_membership m WHERE m.sme_periode_id=%s',
                    (int(target_period_id), datetime.now(timezone.utc).replace(tzinfo=None), int(source_period_id)))
                _rebuild_target_membership(settings,cursor,month,int(target_period_id))
            agents = {}
            cursor.execute("SELECT agent_id,effective_from FROM sme_projection_dirty WHERE component='membership' "
                           "AND state='pending' ORDER BY agent_id FOR UPDATE")
            for aid, stamp in cursor.fetchall():
                agents[int(aid)] = min(agents.get(int(aid), stamp), stamp)
    for aid, stamp in sorted(agents.items()):
        apply_membership_for_agent(settings, aid, stamp)


def rebuild_imported_membership(settings, month, period_ids):
    """An imported observation replaces the derived baseline for selected months.

    Caller has already replaced ALL physical careers with the exact imported
    observation, including removal of derived rows absent from that observation.
    This is not a partial restore API. Keep real dated facts global; no imported
    date_reference is used as a day-precision transition.
    """
    from sme_gend.services.membership_scope import membership_projection_enabled
    if not has_import_transaction(settings):
        raise RuntimeError('Le recalcul du mois importé exige sa transaction.')
    if not membership_projection_enabled(settings):
        return
    period_ids = {int(pid) for pid in period_ids}
    if not period_ids:
        return
    placeholders = ','.join(['%s'] * len(period_ids))
    with MySQLConnection(settings).connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM sme_month_membership WHERE sme_periode_id IN ('+placeholders+')',
                           tuple(sorted(period_ids)))
            for pid in sorted(period_ids):
                _rebuild_target_membership(settings,cursor,month,pid)


def validate_membership_snapshot(settings):
    """Read-only semantic check before a staged full restore can be published."""
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id,annee_mois FROM sme_periode')
            periods = {int(pid): canonical_sme_mois_yyyy_mm(str(month)) for pid, month in cursor.fetchall()}
            events = _records(cursor, 'SELECT id,agent_id,category,effective_date FROM agent_effectif_movement '
                              'ORDER BY effective_date DESC,id DESC')
            by_agent = {}
            for event in events:
                by_agent.setdefault(int(event['agent_id']), []).append(event)
            maps = _records(cursor, 'SELECT * FROM sme_month_membership')
            if any(int(row['baseline_member']) not in (0,1) or int(row['is_member']) not in (0,1) for row in maps):
                raise ValueError('Valeur de projection d’effectif invalide (attendu : 0 ou 1).')
            maps = {(int(row['agent_id']), int(row['sme_periode_id'])): row for row in maps}
            cursor.execute('SELECT agent_id,sme_periode_id FROM carriere_situation WHERE sme_periode_id IS NOT NULL')
            careers = {(int(aid), int(pid)) for aid, pid in cursor.fetchall()}
    required = set(maps)
    for aid, timeline in by_agent.items():
        first = min(row['effective_date'] for row in timeline)
        required.update((aid, pid) for pid, month in periods.items() if month and last_day_of_month(month) >= first)
    for aid, pid in sorted(required):
        month = periods.get(pid)
        if not month:
            raise ValueError('Projection d’effectif rattachée à un mois absent ou invalide.')
        event = next((row for row in by_agent.get(aid, ()) if row['effective_date'] <= last_day_of_month(month)), None)
        row = maps.get((aid, pid))
        expected = int(event['category'] == 'GAIN') if event else int(row['baseline_member']) if row else None
        if row is None or int(row['is_member']) != expected or row['event_id'] != (event['id'] if event else None):
            raise ValueError(f'Projection d’effectif incohérente pour l’agent {aid}, mois {month}.')
        if expected and (aid, pid) not in careers:
            raise ValueError(f'Situation mensuelle absente pour un agent comptabilisé ({aid}, {month}).')
    return {'events':len(events),'monthly_memberships':len(maps)}
