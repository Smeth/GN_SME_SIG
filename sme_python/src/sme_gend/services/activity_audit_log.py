"""Read all modules of the authoritative central activity journal."""
from dataclasses import dataclass
import json

from sme_gend.services.sensitive_audit_log import (
    SensitiveAuditQuery, validate_sensitive_audit_query, ACTION_LABELS_FR,
)

MODULE_LABELS = {
    'consultation': 'Consultations et recherches',
    'system': 'Système et imports',
    'auth': 'Comptes et connexion', 'personnel': 'Personnel',
    'sensitive_data': 'Modifications sensibles', 'doc_gen': 'Documents générés',
    'workflow': 'Opérations mensuelles', 'referentiel': 'Référentiels',
    'liste_present': 'Liste des présents', 'configuration': 'Configuration',
}
OPERATION_LABELS = {
    **ACTION_LABELS_FR, 'doc_gen': 'Document généré', 'create': 'Création',
    'update': 'Modification', 'delete': 'Suppression', 'restore': 'Restauration',
    'login': 'Connexion', 'logout': 'Déconnexion', 'login_failed': 'Échec de connexion',
    'subdivisions_merge': 'Fusion de subdivisions', 'subdivision_rename': 'Subdivision renommée',
    'sme_month_fill': 'Mois rempli à partir d’un autre mois',
    'sme_month_emptied': 'Contenu du mois vidé',
    'sme_month_archives_cleaned': 'Nettoyage des archives du mois',
    'mysql_bundle_import_access': 'Import d’archive Access',
    'personnel_dossier_opened': 'Ouverture du dossier agent',
    'section_consulted': 'Consultation d’une section',
    'dossier_tab_consulted': 'Consultation d’une rubrique du dossier',
    'search_results_consulted': 'Consultation de résultats de recherche',
    'database_restored': 'Base restaurée',
    'configuration_restored': 'Configuration partagée restaurée',
    'maintenance_recovered': 'Reprise après maintenance interrompue',
    'activity_journal_export_prepared': 'Export du journal préparé',
    'sme_month_closure_piste_a': 'Clôture mensuelle',
    'sme_month_import_existing_db': 'Import dans le mois existant',
    'login_blocked': 'Connexion bloquée', 'user_locked': 'Compte verrouillé',
    'user_unlocked': 'Compte déverrouillé', 'user_created': 'Compte créé',
    'user_updated': 'Compte modifié', 'user_renamed': 'Compte renommé',
    'user_deleted': 'Compte supprimé', 'password_changed': 'Mot de passe modifié',
    'password_changed_self': 'Mot de passe modifié par l’utilisateur',
    'session_revoked': 'Session révoquée', 'sessions_revoked_all': 'Sessions révoquées',
    'auth_settings': 'Paramètres de connexion modifiés',
    'role_updated': 'Rôle modifié', 'permission_updated': 'Permission modifiée',
}


class ActivityEntries(list):
    """Rows and available operation codes, fetched together in the background."""
    def __init__(self, rows, catalog):
        super().__init__(rows)
        self.catalog = catalog


@dataclass
class ActivityAuditQuery(SensitiveAuditQuery):
    module: str = ''
    cursor_before: tuple | None = None
    snapshot_id: int | None = None


def validate_activity_query(query):
    clean = validate_sensitive_audit_query(query)
    clean.module = str(clean.module or '').strip()
    return clean


def sensitive_export_query(query):
    """The specialized audit must export only its own module."""
    from dataclasses import asdict
    from sme_gend.services.sensitive_audit_log import SENSITIVE_AUDIT_MODULE
    values = asdict(query)
    values['module'] = SENSITIVE_AUDIT_MODULE
    return validate_activity_query(ActivityAuditQuery(**values))


def search_activity_audit(query, *, settings=None):
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_guard import session_can_view_sensitive_audit
    if not session_can_view_sensitive_audit():
        raise PermissionError('Accès refusé au journal d’activité global.')
    q = validate_activity_query(query)
    repository = AppConfigRepository(settings if settings is not None else get_settings())
    keys = repository.audit_personnel_keys(q.personnel_id) if q.personnel_id else None
    rows = repository.search_audit_journal(
        module=q.module or None, action=q.action or None, actor=q.actor or None,
        personnel_id=q.personnel_id or None, personnel_nom=q.personnel_nom or None,
        sme_mois=q.sme_mois or None, date_from=q.date_from, date_to=q.date_to,
        text=q.text or None, limit=q.limit, require_table=True, broad_agent_search=True,
        **({'personnel_keys': keys} if keys is not None else {}),
    )
    return ActivityEntries(rows, repository.list_audit_operations())


def activity_action_label(entry):
    module = str(entry.get('module') or '')
    action = str(entry.get('action') or '')
    return f"{MODULE_LABELS.get(module, module or 'Autre')} — {OPERATION_LABELS.get(action, action)}"


def activity_detail(entry):
    payload = entry.get('payload') or {}
    if not isinstance(payload, dict): return ''
    detail = payload.get('detail') or payload.get('motif') or payload.get('edit_motif')
    if detail: return str(detail)
    if entry.get('module') == 'doc_gen':
        types = {'liste_present': 'Liste des présents', 'liste_personnel': 'Liste du personnel',
                 'dossier_pdf': 'Dossier agent', 'soit_transmis': 'Soit transmis', 'attestation': 'Attestation'}
        kind = str(payload.get('doc_type') or '')
        return f"{types.get(kind, kind.replace('_', ' '))} · {payload.get('format', '')} · {payload.get('count', 0)} personne(s) · {payload.get('dest', '')}"
    values = {k: v for k, v in payload.items() if k not in ('before', 'after', 'changes')}
    if payload.get('changes'):
        values['modifications'] = payload['changes']
    return json.dumps(values or payload, ensure_ascii=False, default=str)[:600]


class ActivityPage(ActivityEntries):
    def __init__(self, rows, catalog, *, snapshot_id, has_more):
        super().__init__(rows, catalog)
        self.snapshot_id = snapshot_id
        self.has_more = has_more
        self.next_cursor = (rows[-1]['ts_utc'], rows[-1]['journal_id']) if rows and has_more else None


def search_activity_page(query, *, settings=None):
    from datetime import datetime, timezone
    from sme_gend.config import get_settings
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_guard import session_can_view_sensitive_audit
    if not session_can_view_sensitive_audit():
        raise PermissionError('Accès refusé au journal d’activité global.')
    q = validate_activity_query(query)
    repository = AppConfigRepository(settings if settings is not None else get_settings())
    if not repository.table_exists('audit_journal'):
        raise RuntimeError('Le journal central est absent de cette base.')
    snapshot = q.snapshot_id
    if snapshot is None:
        with repository._mc.connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT COALESCE(MAX(id),0) FROM audit_journal')
                snapshot = int(cur.fetchone()[0])
    cursor = q.cursor_before
    if cursor:
        stamp = datetime.fromisoformat(str(cursor[0]).replace('Z','+00:00'))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
        cursor = (stamp, int(cursor[1]))
    keys = repository.audit_personnel_keys(q.personnel_id) if q.personnel_id else None
    rows = repository.search_audit_journal(
        module=q.module or None, action=q.action or None, actor=q.actor or None,
        personnel_id=q.personnel_id or None, personnel_nom=q.personnel_nom or None,
        sme_mois=q.sme_mois or None, date_from=q.date_from, date_to=q.date_to,
        text=q.text or None, limit=q.limit+1, require_table=True, broad_agent_search=True,
        personnel_keys=keys, cursor_before=cursor, max_id=int(snapshot), include_id=True,
    )
    # A full DISTINCT catalogue scan is useful only for the first page, not for
    # every chunk of a large export or every Next/Previous navigation.
    catalog = repository.list_audit_operations() if q.cursor_before is None else []
    return ActivityPage(rows[:q.limit], catalog,
                        snapshot_id=snapshot, has_more=len(rows)>q.limit)


def export_activity_csv(query, destination, *, settings):
    """Stream every matching page into an atomic UTF-8 CSV, with full payloads."""
    import csv
    import os
    import tempfile
    from pathlib import Path
    from dataclasses import replace
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.services.auth_guard import session_can_view_sensitive_audit, session_can_export_documents
    if not session_can_view_sensitive_audit() or not session_can_export_documents():
        raise PermissionError('Droits de consultation et d’export du journal requis.')
    target = Path(destination)
    query = replace(query, cursor_before=None, snapshot_id=None)
    count = 0
    fd, name = tempfile.mkstemp(prefix='.journal_', suffix='.tmp', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.writer(stream, delimiter=';')
            writer.writerow(['Identifiant', 'Date UTC', 'Module', 'Opération', 'Utilisateur', 'Mois SME', 'Agent', 'Données complètes'])
            with mysql_import_transaction(settings):
                while True:
                    if not session_can_export_documents():
                        raise PermissionError('Droit d’export retiré ; export annulé.')
                    page = search_activity_page(query, settings=settings)
                    for row in page:
                        values = [row.get(key,'') for key in ('journal_id','ts_utc','module','action','actor','sme_mois_context','personnel_id')]
                        values.append(json.dumps(row.get('payload') or {}, ensure_ascii=False, default=str))
                        # A spreadsheet must never interpret journal text as a formula.
                        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v for v in values])
                        count += 1
                    if not page.has_more:
                        break
                    query = replace(query, cursor_before=page.next_cursor, snapshot_id=page.snapshot_id)
                from dataclasses import asdict
                from sme_gend.services.audit_log import append_audit_event
                filters = asdict(query)
                filters.pop('cursor_before', None)
                filters.pop('snapshot_id', None)
                append_audit_event(action='activity_journal_export_prepared',
                    sme_mois_context=query.sme_mois if len(query.sme_mois)==7 else None,
                    count=count, format='CSV', filters=filters,
                    snapshot_id=page.snapshot_id,
                    detail='Contenu filtré préparé ; la publication du fichier suit cette trace.')
        if not session_can_view_sensitive_audit() or not session_can_export_documents():
            raise PermissionError('Droit de consultation retiré ; export annulé.')
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    return count


def log_dossier_opened(settings, row):
    from sme_gend.repositories.mysql_connection import mysql_import_transaction
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    from sme_gend.services.auth_context import resolve_audit_actor
    from sme_gend.config.sme_context import mois_actif_courant
    with mysql_import_transaction(settings):
        AppConfigRepository(settings).append_audit(action='personnel_dossier_opened',
            module='consultation', actor=resolve_audit_actor(),
            sme_mois_context=settings.mysql_view_mois or mois_actif_courant(),
            personnel_id=str(row.id or ''), payload={
                'personnel_nom':row.noms or '',
                'legacy_agent_id':str(row.satellite_join_key() or ''),
            })
