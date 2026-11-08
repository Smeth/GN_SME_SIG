"""Keep generated position references valid during monthly replacements."""

SOURCE_TABLES = frozenset({
    'agent_diplome', 'agent_distinction', 'agent_passation_grade', 'agent_affectation',
    'agent_mission', 'agent_malade', 'agent_desertion', 'parcours_stage',
    'mouvement_effectif', 'agent_conge', 'agent_position_event', 'carriere_situation',
})


def automatic_source(reference):
    """Return source table/id and the prefix/suffix needed after ID remapping."""
    parts = str(reference).split(':')
    if parts[:2] != ['sgi', 'auto']:
        raise ValueError('Reference automatique non reconnue.')
    if len(parts) == 4 and parts[2] in SOURCE_TABLES:
        return parts[2], int(parts[3]), 'sgi:auto:' + parts[2] + ':', ''
    if len(parts) == 4 and parts[2] == 'position':
        return 'agent_position_event', int(parts[3]), 'sgi:auto:position:', ''
    if len(parts) == 5 and parts[2] == 'activity_return' and parts[3] in {'agent_mission', 'agent_conge', 'agent_desertion', 'parcours_stage', 'agent_malade'}:
        return parts[3], int(parts[4]), 'sgi:auto:activity_return:' + parts[3] + ':', ''
    if len(parts) == 6 and parts[2] == 'stage_end' and parts[4] in {'LOCAL', 'INTERNE', 'EXTERIEUR'}:
        from sme_gend.services.access_date_parse import parse_access_date
        if parse_access_date(parts[5]) is not None:
            return 'carriere_situation', int(parts[3]), 'sgi:auto:stage_end:', ':' + parts[4] + ':' + parts[5]
    raise ValueError('Reference automatique non reconnue.')


def source_match_fields(source):
    return [column for column in source if column.startswith('date_') or column in {
        'type_stage_code', 'type_conge_code', 'reference_dossier', 'reference_decision',
        'libelle_stage', 'session', 'position_admin', 'nature_libelle', 'lieu',
    }]


def external_position_links(cur, plan, month):
    """Capture links in other months before deleting their source rows.

    The join limits each read to sources actually selected by the purge plan;
    it does not perform a query for every historical position in the database.
    """
    found = {}
    for item in plan:
        table = item.table
        if table not in SOURCE_TABLES:
            continue
        matches = [f"pe.reference_decision=CONCAT('sgi:auto:{table}:',d.id)"]
        if table in {'agent_mission', 'agent_conge', 'agent_desertion', 'parcours_stage', 'agent_malade'}:
            matches.append(f"pe.reference_decision=CONCAT('sgi:auto:activity_return:{table}:',d.id)")
        if table == 'agent_position_event':
            matches.append("pe.reference_decision=CONCAT('sgi:auto:position:',d.id)")
        if table == 'carriere_situation':
            matches.append("pe.reference_decision LIKE CONCAT('sgi:auto:stage_end:',d.id,':%%')")
        cur.execute(f'SELECT pe.id,pe.agent_id,pe.reference_decision,d.* FROM {table} d '
                    'JOIN agent_position_event pe ON (' + ' OR '.join(matches) + ') '
                    f'WHERE ({item.predicate}) AND TRIM(REPLACE(COALESCE(pe.mois_sme,\'\'),\'-\',\'\'))<>%s '
                    'FOR UPDATE OF d,pe', (*item.params, month.replace('-', '')))
        names = [column[0] for column in cur.description][3:]
        for row in cur.fetchall():
            found[int(row[0])] = {'id': int(row[0]), 'agent_id': int(row[1]),
                'reference': row[2], 'table': table, 'source': dict(zip(names, row[3:]))}
    return list(found.values())


def remap_external_position_links(cur, dependencies):
    for dependency in dependencies:
        table, _, prefix, suffix = automatic_source(dependency['reference'])
        fields = source_match_fields(dependency['source'])
        conditions = ['agent_id=%s'] + [f'`{column}` <=> %s' for column in fields]
        cur.execute(f'SELECT id FROM {table} WHERE ' + ' AND '.join(conditions),
                    (dependency['agent_id'], *(dependency['source'][column] for column in fields)))
        candidates = cur.fetchall()
        if len(candidates) != 1:
            raise ValueError('Un autre mois dépend d’une activité que la restauration ne restitue pas à l’identique. '
                             'Restauration annulée ; aucun retour validé n’a été supprimé.')
        cur.execute('UPDATE agent_position_event SET reference_decision=%s WHERE id=%s AND agent_id=%s',
                    (prefix + str(candidates[0][0]) + suffix, dependency['id'], dependency['agent_id']))
