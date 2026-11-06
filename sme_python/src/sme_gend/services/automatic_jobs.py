"""Complete automatic jobs; CLI entry points never split their commits."""
from dataclasses import replace
from sme_gend.services.automatic_maintenance import automatic_write,identifier
from sme_gend.repositories.mysql_connection import MySQLConnection


@automatic_write
def dedupe_dossier_tables(settings):
    # Equal date does not mean equal diploma/grade. Keep every distinct value,
    # including provenance and observations. Compare text byte-for-byte.
    out={}
    with MySQLConnection(settings).connect() as conn:
        with conn.cursor() as cur:
            for table,key in (('agent_passation_grade','passations_supprimees'),('agent_diplome','diplomes_supprimes')):
                cur.execute('SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME<>%s ORDER BY ORDINAL_POSITION',(table,'id'))
                columns=[row[0] for row in cur.fetchall()]
                if not columns:raise RuntimeError('Schema des historiques incomplet.')
                equal=' AND '.join('BINARY a.'+identifier(name)+' <=> BINARY b.'+identifier(name) for name in columns)
                cur.execute('DELETE a FROM '+identifier(table)+' a JOIN '+identifier(table)+' b ON a.id<b.id AND '+equal)
                out[key]=int(cur.rowcount)
    return out


@automatic_write
def run_dossier_maintenance(settings,*,mois,skip_dedupe=False,skip_resync=False):
    from sme_gend.services.referentiel_service import ReferentielService
    from sme_gend.services.sot_maintenance import resync_fk_referentiels,project_carriere_from_dossier_batch
    from sme_gend.services.career_import_alerts import scan_all_career_alerts
    from sme_gend.services.sot_status_service import fetch_sot_status_summary
    from collections import Counter
    with MySQLConnection(settings).connect(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='ref_fonction_structure'")
            if not cur.fetchone()[0]:raise RuntimeError('Initialisez le schema applicatif avant la maintenance ; aucun DDL dans le lot metier.')
    report={'ref_fonction_structure':'deja_presente','fonction_structure_links':ReferentielService(settings).fonction_backfill_structure_links()}
    if not skip_dedupe:report['dedupe']=dedupe_dossier_tables(settings)
    if not skip_resync:
        report['fk']=resync_fk_referentiels(settings,mois=mois)
        report['projection']=project_carriere_from_dossier_batch(settings,mois=mois,only_est_actuelle=False)
    view=replace(settings,mysql_view_mois=mois)
    alerts=scan_all_career_alerts(view,mois_focus=mois,limit=5000);summary=fetch_sot_status_summary(view)
    report['apres']=dict(alertes_parcours=len(alerts),alertes_par_code=dict(Counter(alert.code for alert in alerts)),gaps=len(summary.gap_ids),fk=len(summary.fk_unresolved_ids))
    return report


@automatic_write
def run_month_coherence(settings,*,mois,skip_maintenance=False):
    from sme_gend.services.referentiel_maintenance import run_referentiel_maintenance
    from sme_gend.services.sot_maintenance import resync_fk_referentiels,project_carriere_from_dossier_batch
    from sme_gend.services.sot_status_service import fetch_sot_status_summary
    view=replace(settings,mysql_view_mois=mois);before=fetch_sot_status_summary(view)
    out={}
    if not skip_maintenance:
        out['maintenance']=run_referentiel_maintenance(settings,mois=mois,migrate_org=False,resync_fk=False,resync_dossier=False).to_dict()
    out['fk']=resync_fk_referentiels(settings,mois=mois)
    out['projection']=project_carriere_from_dossier_batch(settings,mois=mois,only_est_actuelle=False)
    out['fk_final']=resync_fk_referentiels(settings,mois=mois)
    after=fetch_sot_status_summary(view)
    out.update(avant=dict(gaps=len(before.gap_ids),fk=len(before.fk_unresolved_ids)),apres=dict(gaps=len(after.gap_ids),fk=len(after.fk_unresolved_ids)))
    return out
