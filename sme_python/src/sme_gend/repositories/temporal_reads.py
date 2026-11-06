"""One eligibility rule for dated registers and targeted monthly reads."""
from sme_gend.services.temporal_events import eligibility
from sme_gend.repositories.monthly_write_guard import revision_sql,token_from_row
from sme_gend.models.numerique_satellite import MissionDetail,MaladeDetail,DeserteurDetail,StageDetail
from sme_gend.models.agent_evenement import AgentCongeRow
from sme_gend.services.agent_conge_service import CONGE_TYPE_LABELS


def _text(value):
    if value is None:return None
    return value.isoformat() if hasattr(value,'isoformat') else str(value)


def load_details(repo,family,*,legacy_id=None,type_code=None,by_type=False):
    specs={
        'mission':('agent_mission','date_debut','date_fin','e.nature_libelle,e.lieu,e.reference_dossier,e.date_debut'),
        'malade':('agent_malade','date_debut','date_fin','e.date_debut,e.pathologie,e.observation'),
        'desertion':('agent_desertion','date_desertion',None,'e.date_desertion,e.reference_dossier'),
        'stage':('parcours_stage','date_debut','date_fin','e.nature_libelle,e.lieu,e.observation,e.date_debut,e.date_fin,e.reference_dossier,e.session'),
        'conge':('agent_conge','date_debut','date_fin','e.type_conge_code,e.date_debut,e.date_fin,e.quota_jours,e.observation,p.annee_mois,e.source_code'),
    }
    table,start,end,fields=specs[family]
    with repo._connect(read_only=True) as conn:
        with conn.cursor() as cur:
            manual="e.source_code='saisie'" if family=='conge' else None
            where,params=eligibility(repo._settings,cur,'e',start,end,manual=manual)
            if legacy_id is not None:
                aid=repo._agent_id(cur,legacy_id)
                if not aid:return {}
                where+=' AND e.agent_id=%s';params+= (aid,)
            if type_code:
                where+=' AND e.type_stage_code=%s';params+=(type_code,)
            version=f',e.sme_periode_id,{revision_sql(table)}' if family!='conge' else ''
            cur.execute(f'SELECT a.legacy_access_id,{fields},e.id{version} FROM {table} e JOIN agent a ON a.id=e.agent_id LEFT JOIN sme_periode p ON p.id=e.sme_periode_id WHERE {where} ORDER BY e.{start} DESC,e.id DESC',params)
            result={}
            for r in cur.fetchall():
                key=str(legacy_id if legacy_id is not None else r[0] or '').strip()
                if not key:continue
                if family=='mission':detail=MissionDetail(*map(_text,r[1:5]),write_token=token_from_row(r,5))
                elif family=='malade':detail=MaladeDetail(*map(_text,r[1:4]),write_token=token_from_row(r,4))
                elif family=='desertion':detail=DeserteurDetail(_text(r[1]),reference_dossier=_text(r[2]),write_token=token_from_row(r,3))
                elif family=='stage':
                    dates=' — '.join(_text(d) for d in r[4:6] if d)
                    detail=StageDetail(_text(r[1]),_text(r[2]),dates or _text(r[3]),reference_dossier=_text(r[6]),session=_text(r[7]),write_token=token_from_row(r,8))
                else:
                    if by_type:key=(key,str(r[1]))
                    detail=AgentCongeRow(type_conge=str(r[1]),type_label=CONGE_TYPE_LABELS.get(r[1],str(r[1])),
                        date_debut=_text(r[2]),date_fin=_text(r[3]),quota_jours=f'{r[4]:.1f}' if r[4] is not None else None,
                        observation=_text(r[5]),periode_sme=_text(r[6]),source_code=_text(r[7]),id=int(r[8]))
                result.setdefault(key,detail)
            return result


def load_positions(repo):
    from sme_gend.config.liste_generale_scope import resolve_export_mois
    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    from sme_gend.services.mysql_import_period import first_day_of_month,last_day_of_month
    month=canonical_sme_mois_yyyy_mm(resolve_export_mois(repo._settings) or '')
    if not month:return {}
    with repo._connect(read_only=True) as conn:
        with conn.cursor() as cur:
            query="SELECT a.legacy_access_id,e.position_admin,e.date_debut,e.date_fin,e.lieu,e.reference_decision,e.observation,e.mois_sme FROM agent_position_event e JOIN agent a ON a.id=e.agent_id WHERE e.date_debut<=%s AND (e.date_fin IS NULL OR e.date_fin>=%s) AND (e.reference_decision IS NULL OR e.reference_decision NOT LIKE 'sgi:auto:%%') ORDER BY e.date_debut DESC,e.id DESC"
            cur.execute(query,(last_day_of_month(month),first_day_of_month(month)))
            result={}
            for legacy,label,start,end,place,reference,obs,owner in cur.fetchall():
                key=(str(legacy),str(label).strip().casefold().replace(' ',''))
                result.setdefault(key,(start,end,place,None if (reference or '').startswith('sgi:auto:') else reference,obs))
            return result
