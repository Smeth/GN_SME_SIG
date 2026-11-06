"""Paired CPU benchmark: fixed synthetic population, no SQL/network latency."""
from dataclasses import asdict,fields
import cProfile,json,statistics,time
from pathlib import Path
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import dashboard_service as dashboard
from test_dashboard_kpi_equivalence import original_function


@pytest.mark.parametrize('size',[5000,20000])
def test_paired_kpi_performance_on_identical_populations(tmp_path,monkeypatch,size):
    positions=[None,'Présent','Mission','Malade','Déserteur','Détaché','StageInterne','StageLocal','StageEx','Inst Mise Route','CongéAdmin','CongéExp','Punis','Prison MA']
    rows=[ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|dict(id=str(i),noms='SYNTHETIC',position_admin=positions[i%len(positions)],structure='DETACHE' if i%4==0 else 'DPF'))) for i in range(size)]
    old,namespace=original_function()
    present=lambda value,*args,**kwargs:value in ('Présent','Mission')
    namespace['padmin_counts_as_present']=present;monkeypatch.setattr(dashboard,'padmin_counts_as_present',present)
    writers={'before':old,'after':dashboard.build_main_kpi_cards};samples={key:[] for key in writers};results={}
    for writer in writers.values():writer(size,1,2.0,rows)
    for iteration in range(9):
        for key in (('before','after') if iteration%2==0 else ('after','before')):
            start=time.perf_counter();result=writers[key](size,1,2.0,rows);samples[key].append(time.perf_counter()-start)
            if results:assert result==next(iter(results.values()))
            results[key]=result
    calls={}
    for key,writer in writers.items():
        profiler=cProfile.Profile();profiler.enable();writer(size,1,2.0,rows);profiler.disable()
        calls[key]=sum(entry.callcount for entry in profiler.getstats() if getattr(entry.code,'co_name',None)=='_padmin_eq')
    evidence=dict(rows=size,repeats=9,alternated_order=True,native_mysql=False,presence_predicate_stubbed=True,
        medians={key:statistics.median(values) for key,values in samples.items()},seconds=samples,
        padmin_comparison_calls=calls,all_card_values_and_subtitles_identical=True)
    (tmp_path/f'kpi_cpu_{size}.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
