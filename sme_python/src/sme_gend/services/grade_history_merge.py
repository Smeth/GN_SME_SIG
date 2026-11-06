"""Reconcile archive grade events without choosing a winner by insertion order."""
from types import SimpleNamespace
from sme_gend.services.grade_label_format import format_grade_display


def matching_archive_passation(cur, agent_id, grade, stamp, reference=None):
    """Return a compatible existing event; reject contradictory dated evidence."""
    cur.execute('SELECT code,libelle FROM ref_grade')
    catalogue = [SimpleNamespace(code=code, libelle=label) for code, label in cur.fetchall()]
    normalize = lambda value: format_grade_display(value, catalogue).strip().casefold()
    cur.execute('SELECT * FROM agent_passation_grade WHERE agent_id=%s AND date_passation <=> %s ORDER BY id DESC',
                (agent_id, stamp))
    columns = [column[0] for column in cur.description]
    existing = [dict(zip(columns, row)) for row in cur.fetchall()]
    for row in existing:
        # Undated events cannot establish a contradiction on a particular day.
        if stamp is not None and normalize(row['grade_libelle']) != normalize(grade):
            raise ValueError(f'Conflit de passation pour l’agent {agent_id}, le {stamp} : '
                f'dossier « {row["grade_libelle"]} », archive « {grade} ». '
                'Vérifiez la date et la décision dans le dossier. Aucune restauration/importation validée.')
        if normalize(row['grade_libelle']) == normalize(grade):
            old_ref, new_ref = str(row.get('ndecision') or '').strip(), str(reference or '').strip()
            if stamp is not None and old_ref and new_ref and old_ref.casefold() != new_ref.casefold():
                raise ValueError(f'Conflit de référence de passation pour l’agent {agent_id}, le {stamp} : '
                    f'« {old_ref} » / « {new_ref} ». Vérifiez le dossier avant de restaurer/importer.')
    return next((row for row in existing if normalize(row['grade_libelle']) == normalize(grade)), None)


def reconcile_restored_grades(settings, careers, month):
    from sme_gend.repositories.mysql_connection import MySQLConnection
    from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
    from sme_gend.services.access_date_parse import parse_access_date
    repo = MySQLAgentDossierRepository(settings)
    corrections = []
    for cid, aid in careers:
        cutoff = repo._carriere_cutoff(str(cid))
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT grade_libelle,date_grade,ndecision FROM carriere_situation WHERE id=%s', (cid,))
                grade, stamp, reference = cur.fetchone()
                stamp = parse_access_date(stamp)
                before = (grade, stamp, reference)
        if grade and stamp and stamp <= cutoff:
            repo.insert_passation_if_absent(aid, grade_libelle=grade, date_passation=stamp,
                ndecision=reference, obs='Import archive ' + month + '\nRestauration mensuelle')
        with MySQLConnection(settings).connect() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT grade_libelle,date_passation,ndecision FROM agent_passation_grade '
                    'WHERE agent_id=%s AND date_passation<=%s ORDER BY date_passation DESC,id DESC LIMIT 1', (aid, cutoff))
                latest = cur.fetchone()
                if latest:
                    matching_archive_passation(cur, aid, latest[0], latest[1], latest[2])
        if latest:
            repo.sync_carriere_grade_from_passations(aid, str(cid), cutoff=cutoff)
            with MySQLConnection(settings).connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT grade_libelle,date_grade,ndecision FROM carriere_situation WHERE id=%s', (cid,))
                    grade_after, date_after, ref_after = cur.fetchone()
            after = (grade_after, parse_access_date(date_after), ref_after)
            if before != after:
                from sme_gend.services.business_audit import audit_values
                corrections.append(audit_values({'carriere_id': cid, 'agent_id': aid,
                    'month': month, 'before': dict(zip(('grade', 'date_grade', 'ndecision'), before)),
                    'after': dict(zip(('grade', 'date_grade', 'ndecision'), after))}))
    return corrections
