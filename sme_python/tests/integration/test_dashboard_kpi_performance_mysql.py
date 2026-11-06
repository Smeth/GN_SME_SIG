"""Measured cached-personnel benchmark; native MySQL context, no real data."""
from pathlib import Path
import json
import pymysql
import pytest
from test_restore_mysql import isolated_mysql,_month_lot26_setup

@pytest.mark.parametrize('archive',[False,True])
def test_performance_lot29_dashboard_kpi_benchmark(isolated_mysql,monkeypatch,tmp_path,archive):
    import cProfile,hashlib,pstats,statistics,time
    from dataclasses import asdict,replace
    from types import SimpleNamespace
    from sme_gend.services import dashboard_service as dash,liste_generale_cache
    from sme_gend.config import sme_context,liste_generale_scope
    from sme_gend.models.liste_generale import ListeGeneraleRow
    settings,connect,params,closure,workspace=_month_lot26_setup(isolated_mysql,monkeypatch,tmp_path)
    data_settings=replace(settings,mysql_view_mois='2026-08' if archive else None)
    rows=[ListeGeneraleRow(id=str(i),noms='SYNTHETIC '+str(i),date_naissance=None,lieu_naissance=None,grade='G'+str(i%12),date_grade=None,date_service=None,ndecision=None,matricule_militaire=None,matricule_solde=None,position_admin=[None,'Présent','Mission','Malade','Déserteur','Détaché','StageInterne','StageLocal','StageEx','Inst Mise Route','CongéAdmin','CongéExp','Punis','Prison MA'][i%14],fonction=None,subdivision_arme=None,diplome=None,date_obtention=None,structure='S'+str(i%35),obs=None,ordre_tri=None,presseance=None) for i in range(5000)]
    service=dash.DashboardService.__new__(dash.DashboardService)
    service._settings=settings;service._data_settings=data_settings
    service._liste=SimpleNamespace(fetch_all=lambda:rows)
    service._structures=SimpleNamespace(fetch_all=lambda:[]);service._grades=SimpleNamespace(fetch_all=lambda:[])
    service._sme=SimpleNamespace(table_exists=lambda:False)
    service._gain_perte=SimpleNamespace(fetch_for_month=lambda month:[])
    service._snapshot_cache_key=None;service._snapshot_cache_val=None
    monkeypatch.setattr(liste_generale_cache,'get_liste_rows',lambda st,loader:rows)
    monkeypatch.setattr(dash,'padmin_counts_as_present',lambda value,*a,**kw:value in ('Présent','Mission'))
    monkeypatch.setattr(dash,'build_career_alerts_kpi_card',lambda *a,**kw:None)
    monkeypatch.setattr(dash,'build_conge_long_alert_kpi_card',lambda *a,**kw:None)
    monkeypatch.setattr('sme_gend.repositories.subdivision_repository.SubdivisionRepository',lambda st:SimpleNamespace(fetch_flat_hierarchy=lambda:[]))
    counts={'context':0,'sql':0};original=sme_context.load_sme_context;execute=pymysql.cursors.Cursor.execute
    def context():counts['context']+=1;return original()
    def observed(cursor,query,args=None):counts['sql']+=1;return execute(cursor,query,args)
    monkeypatch.setattr(sme_context,'load_sme_context',context);monkeypatch.setattr(liste_generale_scope,'load_sme_context',context)
    monkeypatch.setattr(pymysql.cursors.Cursor,'execute',observed)
    def run():service._invalidate_snapshot_cache();return service.snapshot()
    run();times=[];observations=[];digest=None
    for _ in range(6):
        counts.update(context=0,sql=0);started=time.perf_counter();result=run();times.append(time.perf_counter()-started)
        observations.append(dict(counts));current=hashlib.sha256(json.dumps(asdict(result),sort_keys=True,default=str).encode()).hexdigest()
        assert digest in (None,current);digest=current
    profile=cProfile.Profile();profile.enable();run();profile.disable()
    top=sorted(pstats.Stats(profile).stats.items(),key=lambda item:item[1][3],reverse=True)[:12]
    import ast
    source=Path(__file__).resolve().parents[2]
    path='src/sme_gend/services/dashboard_service.py';current_hash=hashlib.sha256((source/path).read_bytes()).hexdigest()
    reference=source/'tests/fixtures/kpi_reference_before_optimization.py'
    def function_tree(file):
        return ast.dump(next(n for n in ast.parse(file.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef) and n.name=='build_main_kpi_cards'),include_attributes=False)
    stage='before' if function_tree(source/path)==function_tree(reference) else 'after'
    evidence=dict(stage=stage,archive=archive,rows=5000,repeats=6,median_seconds=statistics.median(times),seconds=times,
        calls=observations,snapshot_sha256=digest,native_mysql_context=True,synthetic_cached_personnel_and_reference_repositories=True,
        source_sha256=current_hash,profile_top=[dict(file=Path(key[0]).name,line=key[1],function=key[2],calls=value[1],cumulative_seconds=value[3]) for key,value in top])
    (tmp_path/('dashboard_benchmark_'+stage+('_archive' if archive else '_active')+'.json')).write_text(json.dumps(evidence,indent=2),encoding='utf-8')
