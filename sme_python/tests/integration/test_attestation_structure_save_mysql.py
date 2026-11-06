"""Structure labels save independently of legacy grade configuration defects."""
from copy import deepcopy
import pytest
from test_restore_mysql import isolated_mysql, _lot7_dossier
from sme_gend.models.limite_age_config import StructureAttestationOverride
from sme_gend.repositories.app_config_repository import AppConfigRepository
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.services.limite_age_service import LimiteAgeService
from types import SimpleNamespace


def configuration(isolated_mysql, monkeypatch, *, blank=False):
    settings, _, _, _, _ = _lot7_dossier(isolated_mysql, monkeypatch)
    raw = dict(version=1, grades=[dict(grade='LTN', limite_age=55, limite_service=37),
                                 dict(grade='' if blank else 'Lieutenant', limite_age=60, limite_service=42)],
               options=dict(ville='SYN ORIGINAL'), name_formats={},
               structure_format=dict(overrides=[dict(source='DPF', attestation='ancien libellé')]),
               preserved_extension=dict(value='SYN PRESERVE'))
    repository = AppConfigRepository(settings)
    repository.set_json('limite_age_config', deepcopy(raw))
    return settings, repository, raw


@pytest.mark.parametrize('blank', [False, True])
def test_structure_save_preserves_grades_options_and_unknown_fields(isolated_mysql, monkeypatch, blank):
    settings, repository, raw = configuration(isolated_mysql, monkeypatch, blank=blank)
    service = LimiteAgeService()
    cfg = service.load(settings)
    with pytest.raises(ValueError, match='Chaque grade'):
        service.save(cfg, settings)
    assert repository.get_json('limite_age_config') == raw
    cfg.structure_format.overrides = [StructureAttestationOverride('DPF', 'nouveau libellé')]
    cfg.options.ville = 'UNRELATED DRAFT'
    service.save_structure_format(cfg, settings)
    expected = deepcopy(raw)
    expected['structure_format'] = cfg.structure_format.to_dict()
    assert repository.get_json('limite_age_config') == expected
    assert cfg.write_token == fingerprint(expected)
    # A second save of the same loaded editor uses the newly committed version.
    cfg.structure_format.overrides[0].attestation = 'second libellé'
    service.save_structure_format(cfg, settings)
    assert repository.get_json('limite_age_config')['structure_format'] == cfg.structure_format.to_dict()


def test_structure_save_refuses_a_stale_editor_and_keeps_its_draft(isolated_mysql, monkeypatch):
    settings, repository, _ = configuration(isolated_mysql, monkeypatch)
    service = LimiteAgeService()
    first, second = service.load(settings), service.load(settings)
    first.structure_format.overrides[0].attestation = 'FIRST POST'
    service.save_structure_format(first, settings)
    committed = repository.get_json('limite_age_config')
    second.structure_format.overrides[0].attestation = 'SECOND POST DRAFT'
    with pytest.raises(ReferenceConflictError):
        service.save_structure_format(second, settings)
    assert repository.get_json('limite_age_config') == committed
    assert second.structure_format.overrides[0].attestation == 'SECOND POST DRAFT'


@pytest.mark.parametrize('blank', [False,True])
def test_document_options_do_not_validate_or_replace_other_sections(isolated_mysql,monkeypatch,blank):
    settings,repository,raw=configuration(isolated_mysql,monkeypatch,blank=blank)
    raw['options']['unknown_option']='PRESERVE'
    repository.set_json('limite_age_config',raw)
    service=LimiteAgeService();cfg=service.load(settings)
    cfg.options.ville='SYN NEW TOWN'
    cfg.structure_format.overrides=[]  # unrelated in-memory edits must not leak
    service.save_doc_options(cfg,settings)
    stored=repository.get_json('limite_age_config')
    assert stored['options']['ville']=='SYN NEW TOWN' and stored['options']['unknown_option']=='PRESERVE'
    assert {k:v for k,v in stored.items() if k!='options'}=={k:v for k,v in raw.items() if k!='options'}
    assert cfg.write_token==fingerprint(stored)


@pytest.mark.parametrize('blank', [False,True])
def test_name_format_save_only_changes_the_selected_person(isolated_mysql,monkeypatch,blank):
    settings,repository,raw=configuration(isolated_mysql,monkeypatch,blank=blank)
    raw['name_formats']={'OTHER':{'source':'other person','surname_words':1,'extra':'PRESERVE'}}
    repository.set_json('limite_age_config',raw)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda _:True)
    service=LimiteAgeService();cfg=service.load(settings)
    person=SimpleNamespace(noms='SYN FAMILY PERSON',satellite_join_key=lambda:'SYN-1')
    cfg.options.ville='UNRELATED DRAFT'
    result=service.save_name_format(cfg,person,'SYN FAMILY','Person',settings)
    stored=repository.get_json('limite_age_config')
    assert stored['name_formats']['SYN-1']=={'source':'syn family person','surname_words':2}
    assert stored['name_formats']['OTHER']==raw['name_formats']['OTHER']
    assert {k:v for k,v in stored.items() if k!='name_formats'}=={k:v for k,v in raw.items() if k!='name_formats'}
    assert cfg.name_formats==raw['name_formats'] and result.write_token==fingerprint(stored)


@pytest.mark.parametrize('section', ['options','name','grades'])
def test_all_section_saves_preserve_the_shared_version_check(isolated_mysql,monkeypatch,section):
    settings,repository,raw=configuration(isolated_mysql,monkeypatch)
    raw['grades']=[dict(grade='LTN',limite_age=60,limite_service=42)]
    repository.set_json('limite_age_config',raw)
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda _:True)
    service=LimiteAgeService();first,stale=service.load(settings),service.load(settings)
    first.structure_format.overrides[0].attestation='FIRST POST'
    service.save_structure_format(first,settings)
    committed=repository.get_json('limite_age_config')
    person=SimpleNamespace(noms='SYN PERSON',satellite_join_key=lambda:'SYN-1')
    with pytest.raises(ReferenceConflictError):
        if section=='options':service.save_doc_options(stale,settings)
        elif section=='name':service.save_name_format(stale,person,'SYN','Person',settings)
        else:service.save_grades(stale,settings)
    assert repository.get_json('limite_age_config')==committed


def test_sync_repairs_identical_aliases_and_adds_only_missing_grades(isolated_mysql,monkeypatch):
    settings,repository,raw=configuration(isolated_mysql,monkeypatch)
    monkeypatch.setattr('sme_gend.repositories.grade_repository.GradeRepository',lambda _:SimpleNamespace(
        fetch_all=lambda:[SimpleNamespace(libelle=label,pref_ord=i) for i,label in enumerate(('Lieutenant','LTN','SYN NEW GRADE'),1)]))
    service=LimiteAgeService();cfg,added=service.sync_grades_from_referentiel(settings)
    assert added==['SYN NEW GRADE'] and cfg._sync_cleanups
    assert [row.grade for row in cfg.grades]==['LTN','SYN NEW GRADE']
    assert service.find_grade_entry('Lieutenant',cfg) is cfg.grades[0]
    stored=repository.get_json('limite_age_config')
    assert {k:v for k,v in stored.items() if k!='grades'}=={k:v for k,v in raw.items() if k!='grades'}
    assert stored['grades'][1]['limite_age']==stored['grades'][1]['limite_service']==0
    from sme_gend.services.data_sync import read_data_revision
    revision=read_data_revision(settings)
    _,added_again=service.sync_grades_from_referentiel(settings)
    assert added_again==[] and read_data_revision(settings)==revision


def test_conflicting_custom_limits_are_not_automatically_discarded(isolated_mysql,monkeypatch):
    settings,repository,raw=configuration(isolated_mysql,monkeypatch)
    raw['grades']=[dict(grade='SYN GRADE',limite_age=55,limite_service=35),dict(grade='syn grade',limite_age=60,limite_service=40)]
    repository.set_json('limite_age_config',raw)
    with pytest.raises(ValueError,match='Limites contradictoires'):
        LimiteAgeService().sync_grades_from_referentiel(settings)
    assert repository.get_json('limite_age_config')==raw
