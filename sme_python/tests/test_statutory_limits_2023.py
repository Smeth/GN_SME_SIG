"""Article 98 nouveau, JO 23-2023 pp. 822–823, independent expected values."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from sme_gend.services.statutory_limits import article98_limits
from sme_gend.services.limite_age_service import LimiteAgeService
from sme_gend.models.limite_age_config import LimiteAgeConfig, GradeLimiteEntry


@pytest.mark.parametrize('names,expected', [
    (('Général d’armée','Amiral'),(70,52)),
    (('Général de corps d’armée','Vice-amiral d’escadre'),(70,52)),
    (('Général de division','Vice-amiral'),(70,52)),
    (('Général de brigade','Contre-amiral'),(70,52)),
    (('Colonel-major','Capitaine de vaisseau-major'),(65,47)),
    (('Colonel','Capitaine de vaisseau'),(65,47)),
    (('Lieutenant-colonel','Capitaine de frégate'),(65,47)),
    (('Commandant','Capitaine de corvette'),(65,47)),
    (('Capitaine','Lieutenant de vaisseau'),(60,42)),
    (('Lieutenant','Enseigne de vaisseau de 1re classe'),(60,42)),
    (('Sous-lieutenant','Enseigne de vaisseau de 2e classe'),(60,42)),
    (('Adjudant-major','Maître-major','Major'),(58,40)),
    (('Adjudant-chef','Maître principal'),(58,40)),
    (('Adjudant','Premier maître'),(58,40)),
    (('Sergent-chef','Maître','Maréchal des logis-chef'),(55,37)),
    (('Sergent','Second maître','Maréchal des logis'),(55,37)),
    (('Caporal-chef','Quartier-maître de 1re classe'),(50,32)),
    (('Caporal','Quartier-maître de 2e classe'),(50,32)),
    (('Soldat','Matelot'),(50,32)),
])
def test_2023_land_and_naval_limits_match_supplied_table(names,expected):
    from sme_gend.services.limite_age_service import _find_default_limits
    for name in names:
        assert article98_limits(name) == expected
        assert _find_default_limits(name) == expected


def test_loading_old_limits_aligns_only_known_grades_without_overwriting_stored_settings(monkeypatch):
    from sme_gend.config.settings import Settings
    raw = {'grades': [dict(grade='Colonel',limite_age=60,limite_service=42),
                      dict(grade='Grade local',limite_age=59,limite_service=39)],
           'options': {'signataire_nom':'SIGNATAIRE LOCAL','ville':'Dolisie'},
           'name_formats': {'101':{'source':'nom composé jean','surname_words':2}}}
    before = deepcopy(raw)
    monkeypatch.setattr('sme_gend.services.app_config_store.load_config_model',
                        lambda *a,**kw:LimiteAgeConfig.from_dict(raw))
    service = LimiteAgeService()
    monkeypatch.setattr(service,'save',lambda *a:pytest.fail('A read must not write settings'))
    for _ in range(2):
        cfg = service.load(Settings(database_path=Path('unused')))
        assert (cfg.grades[0].limite_age,cfg.grades[0].limite_service) == (65,47)
        assert (cfg.grades[1].limite_age,cfg.grades[1].limite_service) == (59,39)
        assert cfg.options.signataire_nom == 'SIGNATAIRE LOCAL' and cfg.options.ville == 'Dolisie'
        assert cfg.name_formats == before['name_formats']
    assert raw == before


def test_career_date_uses_65_and_47_years_not_pension_thresholds():
    from test_limite_age_print_filtered import _person
    cfg = LimiteAgeConfig(grades=[GradeLimiteEntry('Colonel',65,47)])
    person = replace(_person('AGENT FICTIF'),grade='Colonel',date_naissance=date(1970,1,1),date_service='01/01/1990')
    result = LimiteAgeService().calculate_limit(person,cfg)
    assert result.age_limit_date == date(2035,12,31)
    assert result.service_limit_date == date(2037,12,31)
    assert result.limit_date == date(2035,12,31)


def test_advancement_uses_updated_career_limit():
    from test_avancement import _agent
    from sme_gend.services.avancement_engine import evaluer
    from sme_gend.models.avancement import STATUT_SUIVI
    result = evaluer(_agent(date_naissance=date(1976,6,15),date_grade=date(2024,6,15),
                           date_service=date(2000,1,1),maniere_servir=None,campagne_statut=None),
                     on=date(2026,1,1))
    assert result.statut == STATUT_SUIVI
