"""Portée du réimport : aperçu et contrôle transactionnel avant remplacement."""
from dataclasses import dataclass
import json

from sme_gend.repositories.mysql_connection import MySQLConnection, mysql_database_key, mysql_import_transaction
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.business_audit import audit_values
from sme_gend.services.mysql_import_period import normalize_import_mois
from sme_gend.services.month_purge_plan import month_purge_plan

LABELS = {'carriere_situation': 'Situations des agents (alignées sur le fichier)',
          'agent_diplome': 'Diplômes', 'agent_distinction': 'Distinctions',
          'agent_passation_grade': 'Passations de grade', 'agent_affectation': 'Affectations',
          'agent_mission': 'Missions', 'agent_malade': 'Maladies', 'agent_desertion': 'Désertions',
          'parcours_stage': 'Stages', 'mouvement_effectif': 'Gains et pertes',
          'agent_conge': 'Congés', 'agent_position_event': 'Fiches de position',
          'workflow_task': 'Tâches', 'career_alert_ack': 'Acquittements d’alertes',
          'career_alert_ack_occurrence': 'Occurrences d’alertes acquittées'}
EXTRA_TABLES = ('workflow_task', 'career_alert_ack', 'career_alert_ack_occurrence')


@dataclass(frozen=True)
class ImportReplacementPreview:
    mois: str
    preserve_manual: bool
    owner: tuple
    revision: str
    counts: tuple


def authorize_replacement(preserve_manual):
    from sme_gend.services.auth_guard import session_has_admin_role
    from sme_gend.services.workflow_permissions import session_can_perform_action
    if not session_can_perform_action('workflow.import_archive'):
        raise PermissionError('Import de carrière non autorisé.')
    if not preserve_manual and not session_has_admin_role():
        raise PermissionError('Le remplacement complet est réservé aux administrateurs.')


def preview_import_replacement(settings, mois, *, preserve_manual=True, current=False):
    authorize_replacement(preserve_manual)
    ym = normalize_import_mois(mois)
    counts, observed = [], []
    suffix = ' FOR UPDATE' if current else ''
    with mysql_import_transaction(settings):
        with MySQLConnection(settings).connect(read_only=not current) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT id,annee_mois,statut FROM sme_periode WHERE annee_mois=%s' + suffix, (ym,))
                period = cur.fetchone()
                observed.append(period)
                # Même sans période créée, des événements du dossier peuvent
                # déjà porter une date ou une provenance correspondant à ce mois.
                pid = int(period[0]) if period else -1
                for item in month_purge_plan(pid, ym, imported_only=preserve_manual):
                    # La synchronisation de la liste peut modifier/retirer
                    # aussi les situations créées par une reprise sans import.
                    predicate, args = (('d.sme_periode_id=%s', (pid,))
                                       if item.table == 'carriere_situation' else (item.predicate, item.params))
                    cur.execute(f'SELECT d.* FROM {item.table} d WHERE {predicate} ORDER BY d.id' + suffix, args)
                    rows = cur.fetchall()
                    counts.append((item.table, len(rows)))
                    observed.append((item.table, rows))
                if not preserve_manual:
                    for table in EXTRA_TABLES:
                        order = ('id' if table == 'workflow_task' else
                                 'agent_legacy_id,alert_code,mois' + (',fingerprint' if table.endswith('_occurrence') else ''))
                        cur.execute(f'SELECT * FROM {table} WHERE mois=%s ORDER BY {order}' + suffix, (ym,))
                        rows = cur.fetchall()
                        counts.append((table, len(rows)))
                        observed.append((table, rows))
    revision = fingerprint(json.loads(json.dumps(audit_values(observed), default=str)))
    return ImportReplacementPreview(ym, preserve_manual, mysql_database_key(settings), revision, tuple(counts))


def check_import_replacement(settings, preview, *, mois, preserve_manual):
    if (not isinstance(preview, ImportReplacementPreview) or preview.owner != mysql_database_key(settings)
            or preview.mois != normalize_import_mois(mois) or preview.preserve_manual != preserve_manual):
        raise ValueError('Le mois ou le mode d’import a changé. Relancez l’aperçu.')
    live = preview_import_replacement(settings, mois, preserve_manual=preserve_manual, current=True)
    if live.revision != preview.revision:
        raise ReferenceConflictError('Les données du mois ont changé depuis l’aperçu. Import annulé ; relancez l’aperçu.')
