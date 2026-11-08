"""Real dated workforce movements, separate from monthly Access observations."""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import wraps

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.personnel_service import _personnel_write
from sme_gend.services.operation_progress import operation_progress, current_operation_progress
from sme_gend.services.operation_commit_progress import finish_after_commit


TABLE = 'agent_effectif_movement'
COLUMNS = ('m.id,m.agent_id,m.category,m.movement_kind,m.effective_date,m.recorded_at,'
           'm.recorded_by,m.reference_note,m.details,m.version,a.legacy_access_id,a.noms')


def _movement_progress(function):
    @wraps(function)
    def run(*args, **kwargs):
        with operation_progress((('prepare', 'Contrôle du mouvement et de l’agent'),
                                 ('write', 'Enregistrement du mouvement daté'),
                                 ('project', 'Recalcul des situations concernées'),
                                 ('journal', 'Journalisation de la modification'),
                                 ('commit', 'Validation de la transaction MySQL'))) as progress:
            progress.start('prepare', detail='Vérification des droits, de la date et de la version…')
            result = function(*args, **kwargs)
            finish_after_commit(args[0] if args else kwargs['settings'], progress, 'commit',
                                detail='Mouvement et situations concernées enregistrés.')
            return result
    return run


class MovementConflict(ValueError):
    """The selected source changed, or another movement already owns this day."""


@dataclass(frozen=True)
class DatedEffectifMovement:
    id: int
    agent_id: int
    category: str
    movement_kind: str
    effective_date: date
    recorded_at: datetime
    recorded_by: str
    reference_note: str | None
    details: str | None
    version: int
    legacy_id: str
    agent_name: str
    database: tuple = field(repr=False)


def _allowed(*, write=False, correction=False, export=False):
    from sme_gend.services.auth_guard import session_has_admin_role, session_has_permission
    view = session_has_permission('situation.gain_perte')
    if export:
        view = view or session_has_permission('situation.numerique')
    if not view or write and not session_has_permission('effectifs.edition'):
        raise PermissionError('Droits Gains & pertes et édition requis pour cette action.' if write
                              else 'Droit Gains & pertes requis pour consulter les mouvements datés.')
    if correction and (not session_has_admin_role() or not session_has_permission('workflow.correct_movement')):
        raise PermissionError('La correction des mouvements datés exige un administrateur autorisé.')


def _capability(settings):
    from sme_gend.services.schema_migrations import require_capability
    require_capability(settings, 'dated_movements')


def _text(value, name, maximum, *, required=False):
    value = str(value or '').strip()
    if required and not value:
        raise ValueError(f'{name} est obligatoire.')
    if len(value) > maximum:
        raise ValueError(f'{name} est limité à {maximum} caractères.')
    return value or None


def _values(category, movement_kind, effective_date, reference_note, details):
    category = str(category or '').strip().upper()
    if category not in ('GAIN', 'PERTE'):
        raise ValueError('Choisissez GAIN ou PERTE.')
    stamp = parse_access_date(effective_date)
    if stamp is None or stamp.year < 1000 or stamp > date.today():
        raise ValueError('Une date réelle valide et non future est obligatoire.')
    return (category, _text(movement_kind, 'Le motif du mouvement', 64, required=True), stamp,
            _text(reference_note, 'La Réf/note', 256), _text(details, 'Les précisions', 10000))


def _read(settings, cursor, movement_id, *, lock=False):
    cursor.execute('SELECT ' + COLUMNS + f' FROM {TABLE} m JOIN agent a ON a.id=m.agent_id WHERE m.id=%s'
                   + (' FOR UPDATE OF m' if lock else ''), (int(movement_id),))
    row = cursor.fetchone()
    if row is None:
        raise MovementConflict('Mouvement introuvable. Rechargez le registre.')
    return _movement(settings, row)


def _movement(settings, row):
    return DatedEffectifMovement(*row[:10], str(row[10] or ''), str(row[11] or ''), mysql_database_key(settings))


def _state(value):
    if value is None:
        return None
    return {key: getattr(value, key) for key in ('id', 'agent_id', 'category', 'movement_kind',
            'effective_date', 'recorded_at', 'recorded_by', 'reference_note', 'details', 'version')}


def _check_snapshot(settings, snapshot):
    if (not isinstance(snapshot, DatedEffectifMovement) or snapshot.database != mysql_database_key(settings)
            or snapshot.id < 1 or snapshot.agent_id < 1):
        raise MovementConflict('Rechargez la version du mouvement dans cette base avant de continuer.')


def _declared_date_guard(settings, stamp, reason):
    from sme_gend.services.active_month_guard import load_active_month_snapshot
    active = load_active_month_snapshot(settings).active_month
    if not active:
        raise ValueError('Un mois de travail ouvert est requis pour enregistrer un mouvement daté.')
    if stamp.strftime('%Y-%m') != active:
        _allowed(write=True, correction=True)
        if not reason:
            raise ValueError('Un motif de saisie historique est obligatoire en dehors du mois de travail.')


def _day_available(cursor, agent_id, stamp, *, excluding=None):
    cursor.execute(f'SELECT id,category FROM {TABLE} WHERE agent_id=%s AND effective_date=%s FOR UPDATE',
                   (int(agent_id), stamp))
    for movement_id, category in cursor.fetchall():
        if movement_id != excluding:
            raise MovementConflict('Un mouvement ' + str(category) + ' existe déjà pour cet agent à cette date. '
                                   'Corrigez cette fiche ; deux mouvements le même jour sont ambigus.')


def _journal(settings, before, after, reason):
    from sme_gend.services.temporal_changes import record_source_change
    from sme_gend.services.membership_projection import apply_membership_for_agent
    from sme_gend.services.business_audit import record_business_action
    value = after or before
    since = min(item.effective_date for item in (before, after) if item is not None)
    progress = current_operation_progress()
    progress.finish('write', detail='Mouvement enregistré dans la transaction.')
    progress.start('project', detail=f'Recalcul des situations à partir du {since:%d/%m/%Y}…')
    record_source_change(settings, value.agent_id, TABLE, value.id, _state(before), _state(after),
                         reason=reason, effective_from=since, enabled=True)
    apply_membership_for_agent(settings, value.agent_id, since)
    progress.finish('project', detail='Situations concernées recalculées ; transaction non encore validée.')
    progress.start('journal', detail='Enregistrement de la trace métier…')
    record_business_action(settings, action='dated_effectif_movement_' + ('created' if before is None else
        'deleted' if after is None else 'corrected'), module='personnel', entity_id=value.id,
        before=_state(before), after={'movement': _state(after), 'reason': reason},
        personnel_id=str(value.agent_id), personnel_nom=value.agent_name, mois=value.effective_date.strftime('%Y-%m'))
    progress.finish('journal', detail='Modification journalisée dans la transaction.')
    progress.start('commit', detail='Validation du mouvement, des projections et du journal…')


def resolve_selected_agent(settings, legacy_id):
    """A picker returns an Access key; never interpret that key as canonical ID."""
    _allowed()
    _capability(settings)
    key = str(legacy_id or '').strip()
    if not key:
        raise ValueError('Choisissez un agent.')
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id FROM agent WHERE legacy_access_id=%s', (key,))
            found = cursor.fetchone()
            if not found:
                cursor.execute("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() "
                               "AND TABLE_NAME='agent_identity_alias'")
                if cursor.fetchone():
                    cursor.execute('SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s', (key,))
                    found = cursor.fetchone()
    if not found:
        raise ValueError('Cet agent est introuvable. Rechargez la recherche.')
    return int(found[0])


def list_movements(settings, month=None, agent_id=None, limit=200, offset=0):
    _allowed()
    _capability(settings)
    return _list(settings, month, agent_id, limit, offset)


def _list(settings, month, agent_id, limit, offset):
    if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
        raise ValueError('Pagination des mouvements invalide.')
    terms, params = [], []
    if month:
        from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
        from sme_gend.services.mysql_import_period import first_day_of_month, last_day_of_month
        month = canonical_sme_mois_yyyy_mm(str(month))
        if not month:
            raise ValueError('Le mois doit être valide, au format AAAA-MM.')
        terms.append('m.effective_date BETWEEN %s AND %s')
        params.extend((first_day_of_month(month), last_day_of_month(month)))
    if agent_id is not None:
        terms.append('m.agent_id=%s')
        params.append(int(agent_id))
    query = 'SELECT ' + COLUMNS + f' FROM {TABLE} m JOIN agent a ON a.id=m.agent_id'
    if terms:
        query += ' WHERE ' + ' AND '.join(terms)
    query += ' ORDER BY m.effective_date DESC,m.id DESC LIMIT %s OFFSET %s'
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, (*params, limit, offset))
            return [_movement(settings, row) for row in cursor.fetchall()]


@_movement_progress
def create_movement(settings, agent_id, category, movement_kind, effective_date, *, reference_note=None,
                    details=None, reason=''):
    _allowed(write=True)
    values = _values(category, movement_kind, effective_date, reference_note, details)
    reason = _text(reason, 'Le motif de saisie', 2000)
    _capability(settings)
    from sme_gend.repositories.write_guard import lock_agent
    from sme_gend.services.auth_context import resolve_write_actor
    with _personnel_write(settings, reason='dated_effectif_movement_created'):
        _declared_date_guard(settings, values[2], reason)
        if lock_agent(settings, agent_id=int(agent_id)) is None:
            raise ValueError('Agent introuvable. Rechargez la recherche.')
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                _day_available(cursor, agent_id, values[2])
                current_operation_progress().finish('prepare', detail='Agent et date contrôlés.')
                current_operation_progress().start('write', detail='Création du mouvement daté…')
                cursor.execute(f'INSERT INTO {TABLE}(agent_id,category,movement_kind,effective_date,recorded_at,'
                    'recorded_by,reference_note,details,version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,1)',
                    (int(agent_id), *values[:3], datetime.now(timezone.utc).replace(tzinfo=None),
                     resolve_write_actor(None), *values[3:]))
                result = _read(settings, cursor, cursor.lastrowid)
        _journal(settings, None, result, reason)
        return result


@_movement_progress
def update_movement(settings, snapshot, *, category, movement_kind, effective_date, reference_note=None,
                    details=None, reason):
    _allowed(write=True, correction=True)
    _check_snapshot(settings, snapshot)
    values = _values(category, movement_kind, effective_date, reference_note, details)
    reason = _text(reason, 'Le motif de correction', 2000, required=True)
    _capability(settings)
    from sme_gend.repositories.write_guard import lock_agent
    with _personnel_write(settings, reason='dated_effectif_movement_corrected'):
        _declared_date_guard(settings, values[2], reason)
        if lock_agent(settings, agent_id=snapshot.agent_id) is None:
            raise MovementConflict('Agent introuvable. Rechargez le registre.')
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                before = _read(settings, cursor, snapshot.id, lock=True)
                if before.version != snapshot.version or before.agent_id != snapshot.agent_id:
                    raise MovementConflict('Ce mouvement a changé depuis son affichage. Rechargez avant de corriger.')
                _day_available(cursor, snapshot.agent_id, values[2], excluding=snapshot.id)
                current_operation_progress().finish('prepare', detail='Version, agent et date contrôlés.')
                current_operation_progress().start('write', detail='Correction du mouvement daté…')
                cursor.execute(f'UPDATE {TABLE} SET category=%s,movement_kind=%s,effective_date=%s,'
                    'reference_note=%s,details=%s,version=version+1 WHERE id=%s AND version=%s',
                    (*values, snapshot.id, snapshot.version))
                if cursor.rowcount != 1:
                    raise MovementConflict('Modification concurrente : rechargez le mouvement.')
                after = _read(settings, cursor, snapshot.id)
        _journal(settings, before, after, reason)
        return after


@_movement_progress
def delete_movement(settings, snapshot, *, reason):
    _allowed(write=True, correction=True)
    _check_snapshot(settings, snapshot)
    reason = _text(reason, 'Le motif de suppression', 2000, required=True)
    _capability(settings)
    from sme_gend.repositories.write_guard import lock_agent
    with _personnel_write(settings, reason='dated_effectif_movement_deleted'):
        _declared_date_guard(settings, snapshot.effective_date, reason)
        if lock_agent(settings, agent_id=snapshot.agent_id) is None:
            raise MovementConflict('Agent introuvable. Rechargez le registre.')
        with MySQLConnection(settings).connect() as connection:
            with connection.cursor() as cursor:
                before = _read(settings, cursor, snapshot.id, lock=True)
                if before.version != snapshot.version or before.agent_id != snapshot.agent_id:
                    raise MovementConflict('Ce mouvement a changé depuis son affichage. Rechargez avant de supprimer.')
                current_operation_progress().finish('prepare', detail='Version et agent contrôlés.')
                current_operation_progress().start('write', detail='Suppression du mouvement daté…')
                cursor.execute(f'DELETE FROM {TABLE} WHERE id=%s AND version=%s', (snapshot.id, snapshot.version))
                if cursor.rowcount != 1:
                    raise MovementConflict('Suppression concurrente : rechargez le mouvement.')
        _journal(settings, before, None, reason)


def export_month_movements(settings, month):
    """Read adapter only; these rows must never enter the editable legacy table."""
    _allowed(export=True)
    _capability(settings)
    from sme_gend.models.gain_perte import GainPerteRow
    from sme_gend.repositories.gain_perte_repository import cdsme_from_mois_ym
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    month = canonical_sme_mois_yyyy_mm(str(month))
    if not month:
        raise ValueError('Mois des mouvements datés invalide.')
    movements = []
    offset = 0
    while True:
        page = _list(settings, month, None, 1000, offset)
        movements.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    # Use an observed career at or before this month, never a future snapshot.
    contexts = {}
    agent_ids = sorted({movement.agent_id for movement in movements})
    with MySQLConnection(settings).connect(read_only=True) as connection:
        with connection.cursor() as cursor:
            for offset in range(0, len(agent_ids), 500):
                batch = agent_ids[offset:offset + 500]
                cursor.execute('SELECT c.agent_id,p.annee_mois,c.id,'
                    'IF(c.grade_id IS NOT NULL,g.libelle,c.grade_libelle),'
                    'IF(c.structure_id IS NOT NULL,s.code,c.structure_code) FROM carriere_situation c '
                    'JOIN sme_periode p ON p.id=c.sme_periode_id LEFT JOIN ref_grade g ON g.id=c.grade_id '
                    'LEFT JOIN ref_structure s ON s.id=c.structure_id WHERE c.agent_id IN ('
                    + ','.join(['%s'] * len(batch)) + ')', tuple(batch))
                for agent_id, raw_month, career_id, grade, structure in cursor.fetchall():
                    observed = canonical_sme_mois_yyyy_mm(str(raw_month or ''))
                    if observed and observed <= month:
                        previous = contexts.get(int(agent_id))
                        if previous is None or (observed, career_id) > previous[:2]:
                            contexts[int(agent_id)] = (observed, career_id, grade, structure)
    return [GainPerteRow(id=movement.legacy_id or f'agent:{movement.agent_id}',
        grade=contexts.get(movement.agent_id, (None, None, None, None))[2], nom=movement.agent_name,
        objet=movement.category, cdsme=cdsme_from_mois_ym(month), observ=movement.movement_kind,
        mois_ym=month, date_reference=movement.effective_date.isoformat(),
        structure_code=contexts.get(movement.agent_id, (None, None, None, None))[3]) for movement in movements]
