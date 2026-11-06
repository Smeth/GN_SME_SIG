"""Compare complete KPI values/subtitles against the pre-optimization function."""
import ast
from dataclasses import fields
from pathlib import Path
from random import Random
import pytest
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import dashboard_service as dashboard


def original_function():
    path=Path(__file__).resolve().parent/'fixtures/kpi_reference_before_optimization.py'
    node=next(node for node in ast.parse(path.read_text(encoding='utf-8-sig')).body if isinstance(node,ast.FunctionDef) and node.name=='build_main_kpi_cards')
    namespace=dict(dashboard.__dict__)
    exec(compile(ast.Module(body=[node],type_ignores=[]),'pre_optimization_kpi','exec'),namespace)
    return namespace['build_main_kpi_cards'],namespace


@pytest.mark.parametrize('size',[0,1,57,5000])
@pytest.mark.parametrize('mission_present',[False,True])
def test_kpi_counts_labels_order_and_subtitles_equal_previous_behavior(monkeypatch,size,mission_present):
    random=Random(29)
    positions=[None,'Présent',' présent ','PRÉSENT','Mission','Malade','Déserteur','Détaché','Stage Interne',
        'StageLocal','StageEx','Inst Mise Route','CongéAdmin','CongéExp','Punis','Prison MA','UNKNOWN','']
    records=[ListeGeneraleRow(**({field.name:None for field in fields(ListeGeneraleRow)}|
        dict(id=str(i),noms='SYNTHETIC',position_admin=random.choice(positions),structure=random.choice(['DPF','DETACHE','detache','RG'])))) for i in range(size)]
    def present(value,*args,**kwargs):return (value or '').strip().casefold()=='présent' or mission_present and value=='Mission'
    old,namespace=original_function();namespace['padmin_counts_as_present']=present
    monkeypatch.setattr(dashboard,'padmin_counts_as_present',present)
    expected=old(size,17,33.3,records)
    assert dashboard.build_main_kpi_cards(size,17,33.3,records)==expected
