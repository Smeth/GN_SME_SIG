"""Finish a displayed activity, its position and its audit in one transaction."""
from dataclasses import dataclass
from datetime import date

from sme_gend.services.access_date_parse import parse_access_date


ACTIVITIES = {
    'mission': ('agent_mission', 'date_debut', 'date_fin'),
    'conge': ('agent_conge', 'date_debut', 'date_fin'),
    'desertion': ('agent_desertion', 'date_desertion', None),
    'stage': ('parcours_stage', 'date_debut', 'date_fin'),
}
RETURN_TABLES = frozenset({spec[0] for spec in ACTIVITIES.values()} | {'parcours_stage', 'agent_malade'})


@dataclass(frozen=True)
class ActivityCompletionResult:
    event_id: int
    effective_date: date
    position_admin: str
    changed: bool = True


def activity_return_reference(table, event_id):
    if table not in RETURN_TABLES:
        raise ValueError('Type de retour non autorisé.')
    return f'sgi:auto:activity_return:{table}:{int(event_id)}'


def recorded_return(cur, agent_id, table, event_id):
    cur.execute('SELECT date_debut FROM agent_position_event '
                'WHERE agent_id=%s AND reference_decision=%s ORDER BY id DESC LIMIT 1',
                (agent_id, activity_return_reference(table, event_id)))
    row = cur.fetchone()
    return parse_access_date(row[0]) if row else None


def validate_activity_end(value, month, start=None):
    end = parse_access_date(value)
    if end is None:
        raise ValueError('La date réelle de fin / retour est obligatoire.')
    if end > date.today():
        raise ValueError('La date réelle de fin / retour ne peut pas être future.')
    if not month or end.strftime('%Y-%m') != month:
        raise ValueError('Choisissez une date dans le mois consulté, ou ouvrez le mois correspondant.')
    start = parse_access_date(start)
    if start is not None and end < start:
        raise ValueError('La fin / le retour ne peut pas précéder le début de cette activité.')
    return end


def _read_source(settings, cur, agent_id, kind, expected):
    table, _, _ = ACTIVITIES[kind]
    if kind == 'conge':
        event_id = getattr(expected, 'id', None)
        if event_id is None:
            raise ValueError('Version de congé indisponible. Rechargez les données avant de recommencer.')
    else:
        from sme_gend.repositories.monthly_write_guard import revision_sql
        token = getattr(expected, 'write_token', None)
        if token is None or len(token) != 3:
            raise ValueError('Version de fiche indisponible. Rechargez les données avant de terminer cette activité.')
        event_id = int(token[0])
        cur.execute(f'SELECT e.id,e.sme_periode_id,{revision_sql(table)} '
                    f'FROM {table} e WHERE e.id=%s AND e.agent_id=%s FOR UPDATE',
                    (event_id, agent_id))
        current = cur.fetchone()
        if current is None or (int(current[0]), current[1], str(current[2])) != token:
            raise ValueError('Cette fiche a été modifiée ou supprimée sur un autre poste. Rechargez les données.')
    cur.execute(f'SELECT * FROM {table} WHERE id=%s AND agent_id=%s FOR UPDATE',
                (event_id, agent_id))
    names = [column[0] for column in cur.description]
    source = cur.fetchone()
    if source is None:
        raise ValueError('Activité introuvable pour cet agent. Rechargez les données.')
    source = dict(zip(names, source))
    if kind == 'conge':
        from sme_gend.models.agent_evenement import AgentCongeRow
        from sme_gend.repositories.temporal_reads import _text
        from sme_gend.services.agent_conge_service import CONGE_TYPE_LABELS
        cur.execute('SELECT annee_mois FROM sme_periode WHERE id=%s', (source['sme_periode_id'],))
        period = cur.fetchone()
        code = str(source['type_conge_code'])
        current = AgentCongeRow(type_conge=code, type_label=CONGE_TYPE_LABELS.get(code, code),
            date_debut=_text(source['date_debut']), date_fin=_text(source['date_fin']),
            quota_jours=f"{source['quota_jours']:.1f}" if source['quota_jours'] is not None else None,
            observation=_text(source['observation']), periode_sme=_text(period[0]) if period else None,
            source_code=_text(source['source_code']), id=int(source['id']))
        if current != expected:
            raise ValueError('Ce congé a été modifié ou supprimé. Rechargez les données avant de recommencer.')
    # An unchanged displayed row is not enough: another workstation may have
    # added a newer activity with exactly the same administrative position.
    from sme_gend.services.temporal_events import eligibility
    _, start_column, end_column = ACTIVITIES[kind]
    scope, params = eligibility(settings, cur, 'e', start_column, end_column,
                               manual="e.source_code='saisie'" if kind == 'conge' else None)
    cur.execute(f'SELECT e.id FROM {table} e WHERE e.agent_id=%s AND {scope} '
                f'ORDER BY e.{start_column} DESC,e.id DESC LIMIT 1 FOR UPDATE', (agent_id, *params))
    latest = cur.fetchone()
    if latest is None or int(latest[0]) != int(source['id']):
        raise ValueError('Une autre activité a été enregistrée depuis cet affichage. '
                         'Rechargez les données ; la nouvelle activité a été conservée.')
    return source


def complete_activity(settings, row, kind, end_date, *, expected):
    """The date is both the actual finish and the effective return to Présent.

    The linked Présent event also retains desertion returns without adding a
    schema column. Re-edits of the source must keep this recorded completion.
    """
    from sme_gend.services.sensitive_data_policy import assert_may_edit_personnel
    assert_may_edit_personnel(action='enregistrer une fin / un retour d’activité')
    if kind not in ACTIVITIES:
        raise ValueError('Type d’activité non autorisé.')
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    month = canonical_sme_mois_yyyy_mm(resolve_liste_view_mois(settings) or '')
    end = validate_activity_end(end_date, month)
    from sme_gend.services.personnel_service import _personnel_write
    from sme_gend.repositories.write_guard import lock_agent
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
    from sme_gend.services.temporal_events import _event_position, record_position, project_positions
    from sme_gend.services.fonction_structure_policy import fold_label
    from sme_gend.services.sme_editing_policy import is_statut_ferme
    from sme_gend.services.business_audit import record_business_action
    table, start_column, end_column = ACTIVITIES[kind]
    with _personnel_write(settings, reason='activity_completed'):
        aid = lock_agent(settings, carriere_id=row.id)
        if aid is None:
            raise ValueError('Agent introuvable. Rechargez les données.')
        repo = MySQLAgentRepository(settings)
        current = repo.fetch_by_id(row.id)
        if current is None or current != row:
            raise ValueError('La situation de cet agent a changé. Rechargez les données avant de recommencer.')
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT p.annee_mois,p.statut,c.date_fin FROM carriere_situation c '
                            'JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.id=%s FOR SHARE OF p',
                            (int(row.id),))
                period = cur.fetchone()
                if period is None or canonical_sme_mois_yyyy_mm(str(period[0])) != month:
                    raise ValueError('La situation ne correspond plus au mois consulté. Rechargez les données.')
                if is_statut_ferme(period[1] or ''):
                    raise ValueError('Le mois consulté est clôturé. Aucune fin / aucun retour enregistré.')
                before = _read_source(settings, cur, aid, kind, expected)
                event_id = int(before['id'])
                start = parse_access_date(before[start_column])
                if start is None:
                    raise ValueError('Renseignez d’abord la date de début de cette activité.')
                validate_activity_end(end, month, start)
                previous_return = recorded_return(cur, aid, table, event_id)
                if previous_return is not None:
                    if previous_return != end:
                        raise ValueError('Une fin / un retour est déjà enregistré à une autre date. '
                                         'Cette action ne remplace pas un retour déjà validé.')
                    return ActivityCompletionResult(event_id, end, current.position_admin or '', False)
                expected_position = _event_position(table, before)
                if fold_label(current.position_admin or '') != fold_label(expected_position or ''):
                    raise ValueError('Cet agent n’est plus dans cette activité. Rechargez les données ; '
                                     'son autre position administrative a été conservée.')
                from sme_gend.services.mysql_import_period import last_day_of_month
                cutoff = min(last_day_of_month(month), parse_access_date(period[2]) or last_day_of_month(month))
                cur.execute('SELECT reference_decision FROM agent_position_event WHERE agent_id=%s '
                            'AND date_debut<=%s AND (date_fin IS NULL OR date_fin>=%s) '
                            'ORDER BY date_debut DESC,id DESC LIMIT 1 FOR UPDATE', (aid, cutoff, cutoff))
                position_event = cur.fetchone()
                source_reference = f'sgi:auto:{table}:{event_id}'
                if position_event is not None and position_event[0] != source_reference:
                    raise ValueError('Une autre position ou activité a été enregistrée depuis cette fiche. '
                                     'Rechargez les données ; sa position a été conservée.')
                # A return must not hide another unclosed register entry,
                # including after refreshing the newly added activity.
                from sme_gend.services.temporal_events import eligibility
                scope, scope_params = eligibility(settings, cur, 'e', start_column, end_column,
                    manual="e.source_code='saisie'" if kind == 'conge' else None)
                still_open = f'(e.{end_column} IS NULL OR e.{end_column}>%s)' if end_column else (
                    "NOT EXISTS (SELECT 1 FROM agent_position_event completed WHERE completed.agent_id=e.agent_id "
                    "AND completed.reference_decision=CONCAT('sgi:auto:activity_return:agent_desertion:',e.id) "
                    'AND completed.date_debut<=%s)')
                cur.execute(f'SELECT e.id FROM {table} e WHERE e.agent_id=%s AND e.id<>%s AND {scope} '
                            f'AND (e.{start_column} IS NULL OR e.{start_column}<=%s) AND {still_open} '
                            'LIMIT 1 FOR UPDATE', (aid, event_id, *scope_params, end, end))
                if cur.fetchone() is not None:
                    raise ValueError('Plusieurs fiches de cette activité ne sont pas terminées pour cet agent. '
                                     'Faites régulariser leur historique avant d’enregistrer son retour.')
                if end_column:
                    cur.execute(f'UPDATE {table} SET {end_column}=%s WHERE id=%s AND agent_id=%s',
                                (end, event_id, aid))
                record_position(settings, aid, expected_position, start, end,
                                reference=source_reference, baseline='Présent', project=False, automatic_return=False)
                record_position(settings, aid, 'Présent', end,
                                reference=activity_return_reference(table, event_id), project=False)
                project_positions(settings, aid, end)
                fresh = repo.fetch_by_id(row.id)
                if fresh is None:
                    raise RuntimeError('Relecture de la situation impossible ; retour annulé.')
                after = dict(before)
                after.update(date_fin=end, date_retour=end, position_admin=fresh.position_admin,
                             personnel_ids=[str(row.id)])
                record_business_action(settings, action='activity_completed', module=kind,
                                       entity_id=event_id, before=before, after=after, mois=month,
                                       personnel_id=row.satellite_join_key(), personnel_nom=row.noms)
                from sme_gend.services.temporal_changes import record_source_change, acknowledge_projection
                request = record_source_change(settings, aid, table, event_id, before, after,
                    mutation='complete', reason='Fin / retour d’activité', effective_from=end)
                acknowledge_projection(settings, aid, 'position', request)
                return ActivityCompletionResult(event_id, end, fresh.position_admin or '')
