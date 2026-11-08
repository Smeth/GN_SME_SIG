"""Dated facts over an interval, with one explicitly dated headcount reference."""
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from sme_gend.services.operation_progress import operation_progress


# Fixed SQL identifiers only. Repeated identical Access observations are folded.
SOURCES=(
 ('agent_passation_grade','date_passation','Passation de grade',('grade_libelle','ndecision')),
 ('agent_diplome','date_obtention','Diplôme',('categorie','libelle_libre')),
 ('agent_affectation','date_affectation','Affectation',('structure_code','fonction_libelle','subdivision_arme_libelle')),
 ('agent_mission','date_debut','Début de mission',('nature_libelle','lieu','reference_dossier')),
 ('parcours_stage','date_debut','Début de stage',('type_stage_code','nature_libelle','lieu','session')),
 ('agent_malade','date_debut','Début maladie',('pathologie',)),
 ('agent_desertion','date_desertion','Désertion',('reference_dossier',)),
 ('agent_conge','date_debut','Début de congé',('type_conge_code','quota_jours')),
)
MAX_SOURCE_ROWS=50000


def _permissions():
    from sme_gend.services.auth_guard import session_has_permission
    if not(session_has_permission('situation.numerique') and session_has_permission('effectifs.export')):
        raise PermissionError('Droits Situation numérique et export requis pour ce rapport.')


def _date(value):
    from sme_gend.services.access_date_parse import parse_access_date
    result=parse_access_date(value)
    if result is None:raise ValueError('Les deux dates de la période sont obligatoires.')
    return result


def fold_interval_facts(facts):
    unique={}
    for fact in facts:
        signature=(fact['source'],str(fact['agent_id']),fact['date'],fact['kind'],fact['detail'])
        if signature not in unique:
            unique[signature]={**fact,'observations':1,'source_ids':[fact['source_id']]}
        else:
            unique[signature]['observations']+=1
            unique[signature]['source_ids'].append(fact['source_id'])
    groups=Counter((row['source'],str(row['agent_id']),row['date'],row['kind']) for row in unique.values())
    result=[]
    for row in unique.values():
        row['multiple_values']=groups[row['source'],str(row['agent_id']),row['date'],row['kind']]>1
        result.append(row)
    return sorted(result,key=lambda row:(row['date'],row['kind'],row['name'],int(row['agent_id'])))


def capture_period_activity(settings,start,end):
    with operation_progress((('reference', 'Lecture de l’effectif mensuel de référence'),
                             ('sources', 'Lecture des registres datés'),
                             ('fold', 'Regroupement des observations identiques'),
                             ('validate', 'Finalisation de la lecture cohérente'))) as progress:
        progress.start('reference', detail='Vérification de la période et lecture des mois disponibles…')
        result = _capture_period_activity(settings,start,end,progress)
        progress.finish('validate', detail=f'{len(result["events"])} événement(s) daté(s) retenu(s).')
        return result


def _capture_period_activity(settings,start,end,progress):
    _permissions()
    start,end=_date(start),_date(end)
    if start>end:raise ValueError('La date de début doit précéder la date de fin.')
    from sme_gend.repositories.mysql_connection import MySQLConnection,mysql_read_snapshot
    from sme_gend.services.publication_write_barrier import publication_write_barrier
    from sme_gend.services.schema_migrations import capability_available
    from sme_gend.services.month_projection import refresh_month_projection
    from sme_gend.services.mysql_import_period import last_day_of_month
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    from sme_gend.services.migration_reconciliation import capture_month_reconciliation
    migrated=capability_available(settings,'temporal_projection')
    context=publication_write_barrier(settings) if migrated else mysql_read_snapshot(settings)
    with context:
        with MySQLConnection(settings).connect(read_only=True) as connection:
            with connection.cursor() as cursor:
                publication_months=set()
                if capability_available(settings,'publication_registry'):
                    cursor.execute('SELECT mois FROM sme_publication_period WHERE current_validated_id IS NOT NULL')
                    publication_months={str(row[0]) for row in cursor.fetchall()}
                cursor.execute('SELECT p.annee_mois,COUNT(c.id),EXISTS(SELECT 1 FROM import_lot l WHERE l.sme_periode_id=p.id) FROM sme_periode p '
                    'LEFT JOIN carriere_situation c ON c.sme_periode_id=p.id GROUP BY p.id,p.annee_mois,p.statut')
                candidates=[]
                for raw,count,imported in cursor.fetchall():
                    month=canonical_sme_mois_yyyy_mm(str(raw))
                    if month and last_day_of_month(month)<=end and (count or imported or month in publication_months):
                        candidates.append(month)
                reference=max(candidates) if candidates else None
                if migrated and reference:refresh_month_projection(settings,reference)
                snapshot=capture_month_reconciliation(settings,reference) if reference else None
                progress.finish('reference', detail=f'Effectif de référence : {reference or "aucun mois disponible"}.')
                facts=[];undated={}
                # Old Access movement dates were often generated as month boundaries.
                # Preserve their observations without asserting a real effective day.
                cursor.execute('SELECT COUNT(*) FROM mouvement_effectif')
                monthly_observations=int(cursor.fetchone()[0])
                sources=list(SOURCES)
                if capability_available(settings,'dated_movements'):
                    sources.append(('agent_effectif_movement','effective_date','Mouvement effectif',
                                    ('category','movement_kind','reference_note')))
                progress.start('sources', total=len(sources)+1, detail='Lecture des événements datés par registre…')
                for source_index,(table,stamp,label,details) in enumerate(sources,1):
                    progress.update('sources', current=source_index-1, total=len(sources)+1,
                                    detail=f'Registre {source_index}/{len(sources)+1} : {table}…')
                    cursor.execute(f'SELECT COUNT(*) FROM `{table}` WHERE `{stamp}` IS NULL')
                    undated[table]=int(cursor.fetchone()[0])
                    columns=','.join('e.`'+field+'`' for field in details)
                    cursor.execute(f'SELECT e.id,e.agent_id,e.`{stamp}`,a.noms,{columns} '
                        f'FROM `{table}` e JOIN agent a ON a.id=e.agent_id '
                        f'WHERE e.`{stamp}` BETWEEN %s AND %s ORDER BY e.`{stamp}`,e.id LIMIT %s',
                        (start,end,MAX_SOURCE_ROWS+1))
                    records=cursor.fetchall()
                    if len(facts)+len(records)>MAX_SOURCE_ROWS:
                        raise ValueError('La période dépasse 50 000 observations ; choisissez une période plus courte.')
                    for record in records:
                        facts.append(dict(source=table,source_id=int(record[0]),agent_id=int(record[1]),
                            date=_date(record[2]).isoformat(),name=record[3] or '',kind=label,
                            detail=' · '.join(str(value) for value in record[4:] if value is not None and str(value).strip())))
                    progress.update('sources', current=source_index, total=len(sources)+1,
                                    detail=f'{table} : {len(records)} observation(s) datée(s) lue(s).')
                progress.update('sources', current=len(sources), total=len(sources)+1,
                                detail='Lecture des retours explicitement enregistrés…')
                cursor.execute("SELECT e.id,e.agent_id,e.date_debut,e.position_admin,e.reference_decision,a.noms "
                    "FROM agent_position_event e JOIN agent a ON a.id=e.agent_id "
                    "WHERE e.date_debut BETWEEN %s AND %s AND (e.reference_decision LIKE 'sgi:auto:activity_return:%%' "
                    "OR e.reference_decision LIKE 'sgi:auto:stage_end:%%') ORDER BY e.date_debut,e.id LIMIT %s",
                    (start,end,MAX_SOURCE_ROWS+1))
                for key,aid,stamp,position,reference_note,name in cursor.fetchall():
                    facts.append(dict(source='agent_position_event',source_id=int(key),agent_id=int(aid),
                        date=_date(stamp).isoformat(),name=name or '',kind='Retour enregistré',detail=str(position or '')))
                if len(facts)>MAX_SOURCE_ROWS:
                    raise ValueError('La période dépasse 50 000 observations ; choisissez une période plus courte.')
                progress.finish('sources', detail=f'{len(facts)} observation(s) lue(s) dans {len(sources)+1} registre(s).')
        progress.start('fold', total=len(facts), detail='Regroupement des observations historiques identiques…')
        rows=fold_interval_facts(facts)
        progress.finish('fold', detail=f'{len(facts)} observation(s) regroupée(s) en {len(rows)} événement(s).')
        progress.start('validate', detail='Clôture de la lecture cohérente de la période…')
    return {'start':start.isoformat(),'end':end.isoformat(),'reference_month':reference,
        'reference_date':last_day_of_month(reference).isoformat() if reference else None,
        'reference_headcount':len(snapshot.rows) if snapshot else None,
        'reference_totals':snapshot.totals if snapshot else {},'events':rows,'undated_records':undated,
        'monthly_movement_observations':monthly_observations,
        'capture_utc':datetime.now(timezone.utc).isoformat()}


def export_period_activity(settings,start,end,destination):
    with operation_progress((('capture', 'Collecte cohérente des données de la période'),
                             ('workbook', 'Remplissage des trois feuilles Excel'),
                             ('save', 'Enregistrement du classeur Excel'),
                             ('journal', 'Journalisation du rapport enregistré'))) as progress:
        progress.start('capture', detail='Lecture de l’effectif de référence et des événements…')
        result = _export_period_activity(settings,start,end,destination,progress)
        progress.finish('journal', detail='Rapport enregistré ; historique à vérifier.' if result.get('journal_warning')
                        else 'Rapport enregistré et journalisé.')
        return result


def _export_period_activity(settings,start,end,destination,progress):
    data=capture_period_activity(settings,start,end)
    progress.finish('capture', detail=f'{len(data["events"])} événement(s) à exporter.')
    _permissions()
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    from sme_gend.services.csv_safety import spreadsheet_cell
    from sme_gend.services.safe_paths import atomic_destination
    from sme_gend.services.sme_publication_backup import storage_root
    destination=Path(destination).expanduser().resolve()
    if destination.is_relative_to(storage_root().resolve()):
        raise ValueError('Choisissez un dossier d’export en dehors des versions SME archivées.')
    if destination.suffix.lower()!='.xlsx':raise ValueError('Le rapport doit être enregistré en .xlsx.')
    for template in (settings.template_liste_present_path,settings.template_numerique_path,settings.template_detaches_path):
        if template and destination==Path(template).resolve():raise ValueError('Le modèle ne peut pas être remplacé.')
    progress.start('workbook', total=len(data['events']), detail='Création des feuilles de synthèse, événements et dates absentes…')
    book=Workbook();summary=book.active;summary.title='Synthèse période'
    summary.append(['Période',date.fromisoformat(data['start']),date.fromisoformat(data['end'])])
    summary['B1'].number_format=summary['C1'].number_format='dd/mm/yyyy'
    summary.append(['Effectif mensuel de référence',data['reference_headcount'],'Au '+str(data['reference_date'] or 'non disponible')])
    summary.append(['Événements datés enregistrés',len(data['events'])])
    summary.append(['Méthode','Les effectifs mensuels ne sont pas additionnés. Les dates absentes ne sont pas reconstituées.'])
    summary.append(['Dates de fin','Seuls les retours explicitement enregistrés sont comptés comme retours.'])
    summary.append(['Observation','Plusieurs valeurs pour un agent à une date demandent vérification.'])
    summary.append(['Mouvements Access mensuels',data.get('monthly_movement_observations',0),
                    'Observations conservées ; leurs dates de référence ne sont pas des dates réelles certifiées.'])
    for kind,count in sorted(Counter(row['kind'] for row in data['events']).items()):summary.append([kind,count])
    events=book.create_sheet('Événements datés')
    events.append(['Date enregistrée','Agent ID','Nom','Événement','Détail','Observations sources','Plusieurs valeurs','Source','IDs sources'])
    for index,row in enumerate(data['events'],1):
        events.append([date.fromisoformat(row['date']),str(row['agent_id']),spreadsheet_cell(row['name']),
            row['kind'],spreadsheet_cell(row['detail']),row['observations'],'À vérifier' if row['multiple_values'] else '',
            row['source'],','.join(map(str,row['source_ids']))])
        events.cell(events.max_row,1).number_format='dd/mm/yyyy'
        if index%500==0 or index==len(data['events']):
            progress.update('workbook', current=index, total=len(data['events']),
                            detail=f'{index}/{len(data["events"])} événement(s) écrit(s) dans le classeur.')
    missing=book.create_sheet('Dates absentes')
    missing.append(['Registre','Nombre de fiches sans date (toutes périodes)'])
    for source,count in data['undated_records'].items():missing.append([source,count])
    for sheet in book:
        sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1E287D')
        for column,width in {'A':24,'B':24,'C':42,'D':31,'E':70,'F':24,'G':23,'H':27,'I':27}.items():sheet.column_dimensions[column].width=width
    progress.finish('workbook', detail='Trois feuilles remplies et mises en forme.')
    progress.start('save', detail='Écriture et publication atomique du fichier Excel…')
    with atomic_destination(destination,temporary_suffix='.xlsx') as staged:
        book.save(staged);_permissions()
    book.close()
    progress.finish('save', detail='Classeur enregistré sur disque.')
    progress.start('journal', detail='Enregistrement du rapport dans l’historique central…')
    try:
        from sme_gend.services.doc_gen_log import log_doc_generation
        log_doc_generation(doc_type='period_activity',format='xlsx',count=len(data['events']),
            dest_path=str(destination),settings=settings,mois=data['reference_month'],
            meta={'period_start':data['start'],'period_end':data['end'],
                  'reference_date':data['reference_date'],'reference_headcount':data['reference_headcount']})
    except Exception as exc:
        # File creation is already confirmed; a journal failure must not invite
        # the user to repeat an export that succeeded.
        data['journal_warning']='Le rapport est enregistré. Historique central à vérifier : '+str(exc)
    return data
