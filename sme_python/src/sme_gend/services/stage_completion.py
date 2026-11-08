"""Validation of actual completion dates before starting a stage batch."""
from datetime import date
from sme_gend.services.access_date_parse import parse_access_date


def validate_stage_end(value, month, start_dates):
    end = parse_access_date(value)
    if end is None:
        raise ValueError('La date réelle de fin de stage est obligatoire.')
    if end > date.today():
        raise ValueError('Un stage ne peut pas être terminé à une date future.')
    if month and end.strftime('%Y-%m') != month:
        raise ValueError('Choisissez une date de fin dans le mois consulté, ou ouvrez le mois correspondant.')
    for value in start_dates:
        start = parse_access_date(value)
        if start is not None and end < start:
            raise ValueError('La fin de stage ne peut pas précéder le début du stage d’un agent sélectionné.')
    return end.isoformat()


def diploma_display_result(payload, diplomas, diploma_id, cutoff):
    """Explain the existing SME rule; registration and selection are distinct."""
    if payload['categorie'] != 'MILITAIRE':
        return 'diplôme civil conservé au dossier ; la SME affiche uniquement les diplômes militaires'
    obtained = parse_access_date(payload.get('date_obtention'))
    if obtained and obtained > cutoff:
        return 'diplôme conservé au dossier ; sa date d’obtention est postérieure au mois consulté'
    eligible = [d for d in diplomas if d.categorie == 'MILITAIRE'
                and (not d.date_obtention or d.date_obtention <= cutoff)]
    selected = max(eligible, key=lambda d: (d.date_obtention.toordinal() if d.date_obtention else 0, d.id or 0), default=None)
    if selected and selected.id == diploma_id:
        return 'nouveau diplôme affiché dans la SME'
    if selected:
        return f'diplôme conservé au dossier ; la SME conserve « {selected.libelle} », militaire plus récent'
    return 'diplôme enregistré ; actualisez la SME pour vérifier son affichage'


def find_identical_diploma(diplomas, payload):
    from sme_gend.services.fonction_structure_policy import fold_label
    return next((d for d in diplomas if d.categorie == payload['categorie']
        and fold_label(d.libelle) == fold_label(payload['libelle'])
        and d.date_obtention == parse_access_date(payload.get('date_obtention'))), None)


def normalize_validated_stage_return(settings, agent_id, type_code, actual_date, *, source_id=None):
    """A real return replaces the planned next-day fallback; it does not touch diplomas."""
    from sme_gend.repositories.mysql_connection import MySQLConnection, has_import_transaction
    from sme_gend.services.temporal_events import record_position, project_positions, _event_position
    from sme_gend.services.activity_completion import activity_return_reference
    from sme_gend.services.temporal_changes import record_source_change, acknowledge_projection
    if not has_import_transaction(settings):
        raise RuntimeError('Fin de stage sans transaction partagée.')
    end = parse_access_date(actual_date)
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT * FROM parcours_stage WHERE agent_id=%s AND type_stage_code=%s AND date_fin=%s '
                        'ORDER BY id DESC FOR UPDATE', (int(agent_id), type_code, end))
            fields = [column[0] for column in cur.description]
            records = [dict(zip(fields, row)) for row in cur.fetchall()]
            source = next((row for row in records if int(row['id']) == source_id), None)
            if source is None:
                if len(records) != 1:
                    raise ValueError('La fiche de fin de stage est ambiguë. Rechargez les données.')
                source = records[0]
            source_id = int(source['id'])
            start = parse_access_date(source.get('date_debut'))
            base_reference = f'sgi:auto:parcours_stage:{source_id}'
            return_reference = activity_return_reference('parcours_stage', source_id)
            cur.execute('SELECT id FROM agent_position_event WHERE agent_id=%s AND date_debut<=%s '
                        'AND (date_fin IS NULL OR date_fin>=%s) AND '
                        "position_admin NOT IN ('Présent','Present') "
                        'AND (reference_decision IS NULL OR reference_decision NOT IN (%s,%s)) LIMIT 1 FOR UPDATE',
                        (int(agent_id), end, end, base_reference, return_reference))
            if cur.fetchone() is not None:
                raise ValueError('Une autre activité est encore ouverte à la fin saisie. Sa position a été conservée.')
            if start is not None:
                record_position(settings, agent_id, _event_position('parcours_stage', source), start, end,
                    reference=base_reference, project=False, automatic_return=False)
            record_position(settings, agent_id, 'Présent', end,
                reference=return_reference, project=False)
            project_positions(settings, agent_id, end)
            request = record_source_change(settings, agent_id, 'parcours_stage', source_id,
                source, {**source, 'date_retour': end}, mutation='complete', reason='Fin réelle de stage',
                effective_from=end, enabled=True)
            acknowledge_projection(settings, agent_id, 'position', request)
