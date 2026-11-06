"""Reprise mensuelle sans import, sans écrasement ni création d'événements RH.

L'aperçu et l'écriture utilisent le même plan ; celui-ci est recalculé sous le
verrou partagé avant toute insertion. Le mois actif et la source sont conservés.
"""
from dataclasses import dataclass
from datetime import date
import json
import unicodedata

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.business_audit import audit_values
from sme_gend.services.mysql_import_period import (
    normalize_import_mois, first_day_of_month, last_day_of_month,
)

ACTIVITIES = ('parcours_stage', 'agent_mission', 'agent_conge', 'agent_malade')
MONTH_TABLES = ('carriere_situation', *ACTIVITIES, 'agent_desertion', 'mouvement_effectif')
LABELS = {'carriere_situation': 'Situations des agents', 'parcours_stage': 'Stages en cours',
          'agent_mission': 'Missions en cours', 'agent_conge': 'Congés en cours',
          'agent_malade': 'Maladies en cours'}
TECHNICAL = {'id', 'cree_le', 'updated_le', 'created_at', 'updated_at', 'import_lot_id'}


@dataclass(frozen=True)
class FillOptions:
    stages: bool = False
    missions: bool = False
    conges: bool = False
    maladies: bool = False

    def enabled(self, table):
        return getattr(self, dict(zip(ACTIVITIES, ('stages', 'missions', 'conges', 'maladies')))[table])


@dataclass(frozen=True)
class MonthFillPlan:
    source: str
    target: str
    options: FillOptions
    owner: tuple
    revision: str
    counts: tuple
    excluded: int
    review: tuple
    warnings: tuple
    conflicts: tuple


def can_fill_month():
    from sme_gend.services.auth_guard import session_has_admin_role
    from sme_gend.services.workflow_permissions import session_can_perform_action
    return session_has_admin_role() and session_can_perform_action('workflow.fill_month')


def _authorize():
    if not can_fill_month():
        raise PermissionError('La reprise mensuelle exige un administrateur autorisé à importer les données mensuelles.')


def _months(source, target):
    source, target = normalize_import_mois(source), normalize_import_mois(target)
    date.fromisoformat(source + '-01'); date.fromisoformat(target + '-01')
    if target <= source:
        raise ValueError('Le mois à remplir doit être postérieur au mois source.')
    return source, target


def list_month_sources(settings):
    _authorize()
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT p.annee_mois,COUNT(DISTINCT c.agent_id) FROM sme_periode p '
                        'JOIN carriere_situation c ON c.sme_periode_id=p.id '
                        'GROUP BY p.annee_mois ORDER BY p.annee_mois DESC')
            return tuple((normalize_import_mois(str(m)), int(n)) for m, n in cur.fetchall())


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    columns = [c[0] for c in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _period_month(row):
    return normalize_import_mois(str(row[1]))


def _read_state(conn, source, target, *, periods, period_revision, lock=False):
    # periods provient du contrôle atomique de l'état des périodes.
    matching = {ym: [r for r in periods if _period_month(r) == ym] for ym in (source, target)}
    if any(len(rows) > 1 for rows in matching.values()):
        raise ValueError('Période ambiguë : corrigez les doublons de mois avant la reprise.')
    if not matching[source]:
        raise ValueError('Le mois source n’existe pas.')
    source_pid = int(matching[source][0][0])
    target_pid = int(matching[target][0][0]) if matching[target] else None
    source_rows, target_rows = {}, {}
    suffix = ' FOR UPDATE' if lock else ''
    with conn.cursor() as cur:
        for table in MONTH_TABLES:
            source_rows[table] = _rows(cur, f'SELECT * FROM {table} WHERE sme_periode_id=%s ORDER BY id' + suffix, (source_pid,))
            target_rows[table] = (_rows(cur, f'SELECT * FROM {table} WHERE sme_periode_id=%s ORDER BY id' + suffix, (target_pid,))
                                  if target_pid else [])
        # Évite de remplir un mois ayant déjà des éléments manuels du dossier.
        for table, column in (('agent_affectation', 'sme_periode_mois'), ('agent_position_event', 'mois_sme')):
            target_rows[table] = _rows(cur, f'SELECT * FROM {table} WHERE {column}=%s ORDER BY id' + suffix, (target,))
        # Les pertes et les libellés de position déterminent les exclusions.
        positions = _rows(cur, 'SELECT id,libelle,compte_comme_present FROM ref_position_admin ORDER BY id' + suffix)
        agents = _rows(cur, 'SELECT a.id,a.legacy_access_id,a.noms FROM agent a '
                       'WHERE a.id IN (SELECT c.agent_id FROM carriere_situation c WHERE c.sme_periode_id=%s) '
                       'ORDER BY a.id' + suffix, (source_pid,))
        histories={}
        ids=tuple(r['agent_id'] for r in source_rows['carriere_situation'])
        for table in ('agent_diplome','agent_passation_grade','agent_affectation','agent_position_event'):
            histories[table]=_rows(cur,f'SELECT * FROM {table} WHERE agent_id IN ('+','.join(['%s']*len(ids))+') ORDER BY id'+suffix,ids) if ids else []
    return dict(periods=tuple(periods), period_revision=period_revision, source_pid=source_pid, target_pid=target_pid,
                source=source_rows, target=target_rows, positions=positions, agents=agents,histories=histories)


def _norm(value):
    text = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ''.join(c for c in text if not unicodedata.combining(c)).replace(' ', '')


def _ongoing(row, target):
    start, end = parse_access_date(row.get('date_debut')), parse_access_date(row.get('date_fin'))
    if (row.get('date_debut') and start is None) or (row.get('date_fin') and end is None):
        return False
    return (start is None or start <= last_day_of_month(target)) and (end is None or end >= first_day_of_month(target))


def _build(settings, source, target, options, state):
    from sme_gend.services.sme_editing_policy import is_statut_ferme
    from sme_gend.services.padmin_satellite import satellite_kind
    from sme_gend.services.agent_conge_service import padmin_to_conge_type
    conflicts, warnings, review = [], [], []
    if not state['source']['carriere_situation']:
        conflicts.append('Le mois source ne contient aucun agent.')
    occupied = {table: len(rows) for table, rows in state['target'].items() if rows}
    if occupied:
        conflicts.append('Le mois cible contient déjà des données. Aucun ajout ni écrasement n’est autorisé : '
                         + ', '.join(f'{LABELS.get(t, t)} : {n}' for t, n in occupied.items()))
    target_period = next((r for r in state['periods'] if _period_month(r) == target), None)
    if target_period and is_statut_ferme(target_period[2] or ''):
        conflicts.append('Le mois cible est fermé. Rouvrez-le explicitement avant de le remplir.')
    positions = {r['id']: r for r in state['positions']}
    agents = {r['id']: r for r in state['agents']}
    movements = {}
    for row in sorted(state['source']['mouvement_effectif'],
                      key=lambda r: (parse_access_date(r.get('date_reference')) or last_day_of_month(source), r['id'])):
        movements[row['agent_id']] = row
    lost = {aid for aid, row in movements.items()
            if _norm(row.get('categorie_mouvement')) == 'perte' or _norm(row.get('type_mouvement')) == 'perte'}
    eligible, seen = [], set()
    for row in state['source']['carriere_situation']:
        aid = row['agent_id']
        if aid in seen:
            conflicts.append('Plusieurs situations existent pour un agent dans le mois source.')
        seen.add(aid)
        position = positions.get(row.get('position_admin_id'), {}).get('libelle') or row.get('position_admin_libelle')
        ended = parse_access_date(row.get('date_fin'))
        if aid in lost or _norm(position) in {'decede', 'deces', 'retraite', 'radie', 'radiation', 'demission', 'demissionnaire'} or (ended and ended < last_day_of_month(source)):
            continue
        eligible.append(dict(row))
    eligible_ids = {r['agent_id'] for r in eligible}
    selected = {}
    unknown_end = 0
    for table in ACTIVITIES:
        selected[table] = [dict(r) for r in state['source'][table]
                           if options.enabled(table) and r['agent_id'] in eligible_ids and _ongoing(r, target)]
        unknown_end += sum(not r.get('date_fin') for r in selected[table])
    for row in eligible:
        aid = row['agent_id']
        ref = positions.get(row.get('position_admin_id'), {})
        position = ref.get('libelle') or row.get('position_admin_libelle') or ''
        kind = satellite_kind(position)
        conge_type = padmin_to_conge_type(position)
        candidates = []
        if kind:
            table = {'mission': 'agent_mission', 'malade': 'agent_malade'}.get(kind, 'parcours_stage')
            candidates = [r for r in selected[table] if r['agent_id'] == aid]
            if kind.startswith('stage_'):
                code = {'stage_local': 'LOCAL', 'stage_ex': 'EXTERIEUR', 'stage_interne': 'INTERNE'}[kind]
                candidates = [r for r in candidates if r.get('type_stage_code') == code]
        elif conge_type:
            candidates = [r for r in selected['agent_conge'] if r['agent_id'] == aid and r.get('type_conge_code') == conge_type]
        present = bool(ref.get('compte_comme_present')) or _norm(position) in {'present', 'presents'}
        if not present and not candidates:
            row['position_admin_id'] = None
            row['position_admin_libelle'] = 'À vérifier'
            review.append((aid, agents.get(aid, {}).get('noms', str(aid)), position or 'non renseignée'))
    if not eligible and state['source']['carriere_situation']:
        conflicts.append('Aucun agent à reprendre après exclusion des départs et pertes.')
    if review:
        warnings.append(f'{len(review)} position(s) seront marquées « À vérifier » : aucune activité sélectionnée ne justifie leur maintien.')
    if unknown_end:
        warnings.append(f'{unknown_end} activité(s) sans date de fin seront reprises, à confirmer dans le mois cible.')
    warnings.append('Les activités terminées avant le mois cible, gains/pertes, sanctions, tâches et alertes ne sont pas recopiés.')
    if not target_period:
        warnings.append('Le mois cible sera créé « Préparé ». Le mois actif et les statuts des autres périodes ne changent pas.')
    payload = {'carriere_situation': eligible, **selected}
    # Les quotas MySQL sont des Decimal ; leur représentation doit rester stable
    # entre l'aperçu et le nouveau contrôle effectué dans la transaction.
    revision = fingerprint(json.loads(json.dumps(
        audit_values(dict(state=state, source=source, target=target, options=options)), default=str)))
    plan = MonthFillPlan(source, target, options, mysql_database_key(settings), revision,
                         tuple((t, len(rows)) for t, rows in payload.items()),
                         len(state['source']['carriere_situation']) - len(eligible), tuple(review),
                         tuple(warnings), tuple(dict.fromkeys(conflicts)))
    return plan, payload


def preview_month_fill(settings, source, target, options=FillOptions()):
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    _authorize()
    source, target = _months(source, target)
    if not isinstance(options, FillOptions) or any(type(value) is not bool for value in vars(options).values()):
        raise ValueError('Options de reprise invalides.')
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect(read_only=True) as conn:
            snapshot = load_active_month_snapshot(settings)
            state = _read_state(conn, source, target, periods=snapshot.rows, period_revision=snapshot.revision)
            return _build(settings, source, target, options, state)[0]


def month_fill_progress_steps(plan):
    """Completed batches plus backup, checks, period, audit and commit."""
    counts = dict(plan.counts)
    return 5 + sum((n + 49) // 50 for n in counts.values()) + (counts.get('carriere_situation', 0) + 49) // 50


def apply_month_fill(settings, plan, *, progress=None):
    from pathlib import Path
    from sme_gend.services.active_month_guard import period_state_write
    from sme_gend.services.shared_reference_write import mark_shared_data_write
    from sme_gend.services.backup_restore_service import ensure_recent_backup
    from sme_gend.services.business_audit import record_business_action
    from sme_gend.services.mysql_import_period import ensure_sme_periode, refresh_est_actuelle_flags
    _authorize()
    if not isinstance(plan, MonthFillPlan) or plan.owner != mysql_database_key(settings):
        raise ValueError('Le contexte a changé. Relancez l’aperçu de reprise.')
    _months(plan.source, plan.target)
    if plan.conflicts:
        raise ValueError('La reprise comporte des conflits. Aucune donnée n’a été modifiée.')
    report = progress if progress is not None else lambda message: None
    detail = getattr(progress, 'detail', lambda message: None)
    detail('Création de la sauvegarde préalable…')
    backup = ensure_recent_backup(settings, max_age_days=0.0)
    if backup is None or not Path(backup).is_file():
        raise RuntimeError('Reprise annulée : la sauvegarde préalable a échoué.')
    report('Sauvegarde préalable vérifiée')
    _authorize()
    with period_state_write(settings, reason='month_fill', mois=plan.target) as change:
        with MySQLConnection(settings).connect() as conn:
            state = _read_state(conn, plan.source, plan.target, periods=change.before.rows,
                                period_revision=change.before.revision, lock=True)
            current, payload = _build(settings, plan.source, plan.target, plan.options, state)
            if current.revision != plan.revision or current.conflicts:
                raise ReferenceConflictError('Les données ou les périodes ont changé depuis l’aperçu. Aucune reprise effectuée ; relancez l’aperçu.')
            report('Données source et mois cible vérifiés')
            pid = state['target_pid'] or ensure_sme_periode(conn, plan.target, statut='Préparé')
            report('Période cible prête')
            active = change.before.active_month == plan.target
            with conn.cursor() as cur:
                new_careers=[]
                for table, rows in payload.items():
                    detail(f'{LABELS.get(table, table)} : 0 / {len(rows)}')
                    for index, original in enumerate(rows, 1):
                        row = {k: v for k, v in original.items() if k not in TECHNICAL}
                        row['sme_periode_id'] = pid
                        row['import_lot_id'] = None
                        origin = f'Reprise {plan.source} vers {plan.target} ; fiche source {original["id"]}'
                        if table == 'carriere_situation':
                            row.update(est_actuelle=int(active), date_debut=first_day_of_month(plan.target),
                                       date_fin=None if active else last_day_of_month(plan.target))
                            row['obs'] = origin + ('\n' + row['obs'] if row.get('obs') else '')
                        else:
                            # Même marqueur que les saisies : retirer un import ne
                            # doit pas effacer une reprise réalisée sans import.
                            row['source_ligne_legacy'] = f':saisie:r:{original["id"]}'
                            if table == 'agent_conge':
                                row['source_code'] = 'saisie'
                        columns = tuple(row)
                        cur.execute(f'INSERT INTO {table} (' + ','.join(f'`{c}`' for c in columns) + ') VALUES ('
                                    + ','.join(['%s'] * len(columns)) + ')', tuple(row[c] for c in columns))
                        if table=='carriere_situation':new_careers.append((int(cur.lastrowid),int(row['agent_id'])))
                        if index % 50 == 0 or index == len(rows):
                            report(f'{LABELS.get(table, table)} : {index} / {len(rows)}')
            from sme_gend.services.temporal_events import project_available_history
            detail('Mise à jour des situations depuis les historiques…')
            if progress is None:
                project_available_history(settings,new_careers)
            else:
                project_available_history(settings, new_careers,
                    progress=lambda done, total: report(f'Historiques des agents : {done} / {total}')
                        if done % 50 == 0 or done == total else None)
            if active:
                refresh_est_actuelle_flags(conn, plan.target)
            record_business_action(settings, action='sme_month_fill', module='workflow', entity_id=plan.target,
                                   mois=plan.target, after=dict(source=plan.source, target=plan.target,
                                       source_revision=plan.revision,
                                       counts=dict(current.counts), excluded=current.excluded, review=len(current.review),
                                       options=audit_values(plan.options), backup=str(backup)))
            mark_shared_data_write(True)
            report('Journal enregistré ; validation de la transaction…')
    from sme_gend.services.sme_editing_policy import invalidate_editing_state_cache
    invalidate_editing_state_cache()
    report('Remplissage terminé et validé en base')
    return dict(source=plan.source, target=plan.target, counts=dict(current.counts),
                review=len(current.review), backup=str(backup))
