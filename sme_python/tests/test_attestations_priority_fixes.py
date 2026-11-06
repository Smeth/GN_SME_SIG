"""Corrections 2 à 7 sur personnes fictives et copies des modèles."""
from copy import deepcopy
from dataclasses import fields, replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from docx import Document
from docx.oxml import OxmlElement
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.limite_age_config import LimiteAgeConfig, GradeLimiteEntry
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteAgeService


@pytest.fixture(autouse=True)
def authorized_operator(monkeypatch):
    monkeypatch.setattr("sme_gend.ui.document_job.can_export", lambda: True)
    monkeypatch.setattr("sme_gend.ui.views.limite_age_view.can_export", lambda: True)
    monkeypatch.setattr("sme_gend.ui.views.soit_transmis_view.can_export", lambda: True)
    monkeypatch.setattr("sme_gend.ui.views.soit_transmis_view.require_export", lambda parent: True)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def person():
    return ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)} | dict(
        id="SYNTHETIC-1",noms="PERSONNE FICTIVE",grade="Colonel-major",date_naissance=datetime(1966,2,8),
        date_service="24/07/1989",matricule_militaire="FICTIF-MIL",matricule_solde="FICTIF-SOL",structure="UNITE FICTIVE")))


@pytest.fixture
def cfg():
    return LimiteAgeConfig(grades=[GradeLimiteEntry("Colonel-major",60,42)])


@pytest.fixture
def soit(app,monkeypatch):
    from sme_gend.ui.views import soit_transmis_view as ui
    monkeypatch.setattr(ui.SoitTransmisService,"load",lambda *a,**kw:SoitTransmisConfig())
    monkeypatch.setattr("sme_gend.repositories.grade_repository.GradeRepository",lambda *a:SimpleNamespace(fetch_all=lambda:[]))
    view = ui.SoitTransmisView(Settings(database_path=Path("unused")))
    yield view
    view.close()


def test_full_reload_refreshes_checked_objects_and_removes_absent_people(soit,person,monkeypatch):
    from sme_gend.ui.bg_task import run_with_progress
    soit.load_persons([person])
    fresh = replace(person,noms="IDENTITE ACTUALISEE")
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress",lambda *a,**kw:([],[fresh],{}))
    assert soit.load_personnel()
    assert soit._model.checked_rows() == [fresh]
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress",lambda *a,**kw:([],[],{}))
    assert soit.load_personnel()
    assert soit._model.checked_rows() == []
    assert not soit._btn_gen.isEnabled()


def test_new_external_selection_cannot_retain_an_earlier_person(soit,person):
    second = replace(person,id="SYNTHETIC-2",noms="AUTRE PERSONNE")
    soit.load_persons([person])
    soit.load_persons([second])
    assert soit._model.checked_rows() == [second]


def test_external_selection_preserves_all_requested_people_despite_previous_filter(soit,person):
    second = replace(person,id="SYNTHETIC-2",noms="AUTRE PERSONNE")
    soit._search.setText("AUTRE")
    soit.load_persons([person,second])
    assert soit._model.rowCount() == 1
    assert set(p.id for p in soit._model.checked_rows()) == {person.id,second.id}


def test_filter_keeps_checked_people_but_full_refresh_removes_absent_people(soit,person):
    second = replace(person,id="SYNTHETIC-2",noms="AUTRE PERSONNE")
    soit.load_persons([person,second])
    soit._search.setText("AUTRE")
    assert soit._model.rowCount() == 1
    assert soit._model.checked_count() == 2
    soit._model.refresh_checked([second])
    assert soit._model.checked_rows() == [second]


def test_failed_reload_keeps_a_visible_stale_state_and_cannot_generate(soit,person,monkeypatch):
    soit.load_persons([person])
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress",lambda *a,**kw:None)
    assert soit.load_personnel() is False
    assert soit._needs_reload and not soit._fresh
    assert "impossible" in soit._load_status.text()
    assert not soit._btn_gen.isEnabled() and not soit._btn_print_all.isEnabled()
    generator = MagicMock()
    monkeypatch.setattr(soit._svc,"generate_docx",generator)
    soit._generate();soit._open_in_word();soit._open_both()
    generator.assert_not_called()


def test_rebind_during_read_cannot_publish_old_people(soit,person,monkeypatch):
    def changed(*a,**kw):
        soit.rebind_settings(replace(soit._settings,mysql_database="synthetic_other"))
        return [],[person],{}
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress",changed)
    assert soit.load_personnel() is False
    assert not soit._fresh and soit._all_rows == [] and not soit._btn_gen.isEnabled()


def test_limite_age_failure_keeps_retry_and_disables_stale_actions(app,person,cfg,monkeypatch):
    from sme_gend.ui.views import limite_age_view as ui
    view = ui._AttestationsTab(Settings(database_path=Path("unused")),LimiteAgeService())
    view._all_rows = [(person,LimiteAgeService().calculate_limit(person,cfg))]
    view._fresh = True
    view._apply_filter();view._model.check_all();view._table.selectRow(0)
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress",lambda *a,**kw:None)
    assert view.load_personnel() is False
    assert not view._fresh and "impossible" in view._load_status.text()
    assert view._detail._person is None
    assert not view._btn_zip.isEnabled() and not view._btn_print_checked.isEnabled()
    state = SimpleNamespace(_needs_reload=True,_tab_attest=SimpleNamespace(load_personnel=lambda:False),
        _tab_retraite=MagicMock(),_tabs=SimpleNamespace(currentWidget=lambda:None))
    ui.LimiteAgeView._reload_if_needed(state)
    assert state._needs_reload
    view.close()


def test_invalid_person_disables_individual_and_group_generation(app,person,cfg):
    from sme_gend.ui.views import limite_age_view as ui
    person = replace(person,date_naissance=None)
    service = LimiteAgeService()
    view = ui._AttestationsTab(Settings(database_path=Path("unused")),service)
    view._all_rows = [(person,service.calculate_limit(person,cfg))]
    view._fresh = True
    view._apply_filter();view._model.check_all();view._table.selectRow(0)
    assert "naissance" in view._detail._r_retenue.text()
    assert not view._detail._btn_docx.isEnabled() and not view._detail._btn_pdf.isEnabled()
    assert not view._btn_print_checked.isEnabled() and not view._btn_soit_transmis.isEnabled()
    assert not view._btn_zip.isEnabled()
    view.close()


@pytest.mark.parametrize("changes,reason",[
    ({"date_naissance":None},"naissance"),({"date_service":None},"service"),
    ({"date_naissance":"31/02/1966"},"naissance"),({"date_service":"invalide"},"service"),
    ({"date_naissance":date(2090,1,1)},"futur"),({"date_service":"01/01/2090"},"futur"),
    ({"date_service":"01/01/1960"},"postérieure"),({"date_service":"08/02/1966"},"postérieure"),
])
def test_missing_or_impossible_dates_never_generate_an_attestation(person,cfg,tmp_path,monkeypatch,changes,reason):
    svc = LimiteAgeService()
    broken = replace(person,**changes)
    result = svc.calculate_limit(broken,cfg)
    assert result.limit_date is None and any(reason in issue for issue in result.issues)
    target = tmp_path/"unchanged.docx"
    target.write_bytes(b"PREVIOUS")
    with pytest.raises(ValueError,match="Attestation impossible"): svc.generate_docx(broken,cfg,target)
    with pytest.raises(ValueError,match="Attestation impossible"): svc.generate_html(broken,cfg)
    assert target.read_bytes() == b"PREVIOUS"


def test_valid_dates_still_compute_both_limits(person,cfg):
    result = LimiteAgeService().calculate_limit(person,cfg)
    assert result.limit_date == date(2026,12,31)
    assert result.service_limit_date == date(2031,12,31)
    assert result.issues == ()


@pytest.mark.parametrize("age,service",[(-1,42),(121,42),(60,-1),(60,121)])
def test_invalid_configured_bounds_cannot_produce_a_limit(person,age,service):
    cfg = LimiteAgeConfig(grades=[GradeLimiteEntry("Colonel-major",age,service)])
    result = LimiteAgeService().calculate_limit(person,cfg)
    assert result.limit_date is None and any("Barème invalide" in issue for issue in result.issues)


@pytest.mark.parametrize("raw",["Major","Colonel honoraire","", " "])
def test_grade_is_not_guessed_from_a_substring(cfg,raw):
    assert LimiteAgeService().find_grade_entry(raw,cfg) is None


@pytest.mark.parametrize("raw",["CLM","COLONEL MAJOR","Colonel-major"])
def test_exact_known_alias_is_accepted(cfg,raw):
    assert LimiteAgeService().find_grade_entry(raw,cfg) is cfg.grades[0]


def test_ambiguous_grade_aliases_require_configuration_correction():
    cfg = LimiteAgeConfig(grades=[GradeLimiteEntry("LTN",55,37),GradeLimiteEntry("Lieutenant",60,42)])
    assert LimiteAgeService().find_grade_entry("Lieutenant",cfg) is None


def test_loading_imposed_limits_has_no_persistent_write(monkeypatch):
    stored = dict(grades=[dict(grade="Colonel",limite_age=61,limite_service=43)])
    original = deepcopy(stored)
    monkeypatch.setattr("sme_gend.services.app_config_store.load_config_model",lambda *a,**kw:LimiteAgeConfig.from_dict(stored))
    svc = LimiteAgeService()
    save = MagicMock()
    monkeypatch.setattr(svc,"save",save)
    result = svc.load(Settings(database_path=Path("unused")))
    assert (result.grades[0].limite_age,result.grades[0].limite_service) == (65,47)
    assert result._limits_adjusted == ("Colonel",)
    assert stored == original
    save.assert_not_called()


def test_contradictory_imposed_limits_are_rejected_before_save(monkeypatch):
    store = MagicMock()
    monkeypatch.setattr("sme_gend.services.app_config_store.save_config_model",store)
    cfg = LimiteAgeConfig(grades=[GradeLimiteEntry("Colonel",61,43)])
    with pytest.raises(ValueError,match="barème imposé"): LimiteAgeService().save(cfg,Settings(database_path=Path("unused")))
    store.assert_not_called()


def test_imposed_cells_are_locked_and_custom_limits_remain_editable(app,monkeypatch):
    from sme_gend.ui.views import limite_age_view as ui
    cfg = LimiteAgeConfig(grades=[GradeLimiteEntry("Colonel",60,42),GradeLimiteEntry("Grade personnalisé",57,35)])
    svc = MagicMock()
    svc.load.return_value = cfg
    view = ui._ConfigLimitesTab(Settings(database_path=Path("unused")),svc)
    assert not view._table.item(0,1).flags() & Qt.ItemFlag.ItemIsEditable
    assert view._table.item(1,1).flags() & Qt.ItemFlag.ItemIsEditable
    view._table.item(1,0).setText("Adjudant")
    assert view._table.item(1,1).text() == "58"
    assert not view._table.item(1,1).flags() & Qt.ItemFlag.ItemIsEditable
    view.close()


@pytest.fixture
def word(monkeypatch,tmp_path):
    from sme_gend.services import limite_age_service as module
    original = Path(__file__).resolve().parents[1] / "modeles/limite_age.docx"
    model = tmp_path/"modele.docx"
    Document(original).save(model)
    monkeypatch.setattr("sme_gend.config.settings.resolve_modele_file",lambda name:model)
    monkeypatch.setattr(module,"_generate_barcode_png",lambda *a:None)
    monkeypatch.setattr(module,"_resolve_service_line",lambda *a:("","UNITE FICTIVE","UNITE FICTIVE"))
    return model, tmp_path/"output.docx"


def test_shifted_and_reordered_template_keeps_values_under_correct_labels(word,person,cfg):
    model,target = word
    doc = Document(model)
    doc.element.body.insert(0,OxmlElement("w:p"))
    birth = next(p for p in doc.paragraphs if p.text.startswith("Date de naissance"))
    service = next(p for p in doc.paragraphs if p.text.startswith("En service"))
    service._element.addprevious(birth._element)
    doc.save(model)
    LimiteAgeService().generate_docx(person,cfg,target,log_generation=False)
    paragraphs = [p.text for p in Document(target).paragraphs]
    assert next(p for p in paragraphs if p.startswith("En service")).endswith("UNITE FICTIVE")
    assert next(p for p in paragraphs if p.startswith("Date de naissance")).endswith("08/02/1966")
    assert next(p for p in paragraphs if p.startswith("Matricule militaire")).endswith("FICTIF-MIL")


def test_value_replacement_does_not_depend_on_bold_runs(word,person,cfg):
    model,target = word
    doc = Document(model)
    for paragraph in doc.paragraphs:
        if paragraph.text.startswith("Date de naissance") or "31-12-2034" in paragraph.text:
            paragraph.text = paragraph.text
    doc.save(model)
    LimiteAgeService().generate_docx(person,cfg,target,log_generation=False)
    paragraphs = [p.text for p in Document(target).paragraphs]
    assert next(p for p in paragraphs if p.startswith("Date de naissance")).endswith("08/02/1966")
    assert "31-12-2034" not in " ".join(paragraphs)
    assert sum("31-12-2026" in p for p in paragraphs) == 1


@pytest.mark.parametrize('raw,family,given,expected', [
    ('NGOUABI JEAN-PAUL ÉMILE','NGOUABI','Jean-Paul Émile','NGOUABI Jean-Paul Émile'),
    ('MBOUNGOU MAKITA JEAN PAUL','MBOUNGOU MAKITA','Jean Paul','MBOUNGOU MAKITA Jean Paul'),
])
def test_attestation_formats_surname_and_given_names_in_word_and_pdf_html(word,person,cfg,raw,family,given,expected):
    from sme_gend.services.person_name_display import set_name_boundary
    model,target = word
    person = replace(person, noms=raw)
    set_name_boundary(cfg,person,family,given)
    service = LimiteAgeService()
    service.generate_docx(person,cfg,target,log_generation=False)
    text = ' '.join(p.text for p in Document(target).paragraphs)
    assert expected in text
    assert expected in service.generate_html(person,cfg)
    assert person.noms == raw


@pytest.mark.parametrize("change",["missing","duplicate","unexpected_limit_text"])
def test_incompatible_template_never_overwrites_existing_document(word,person,cfg,change):
    model,target = word
    doc = Document(model)
    birth = next(p for p in doc.paragraphs if p.text.startswith("Date de naissance"))
    if change == "missing": birth.text = "Repère inconnu"
    elif change == "duplicate": doc.element.body.insert(0,deepcopy(birth._element))
    else:
        label = next(p for p in doc.paragraphs if p.text.startswith("Atteindra"))
        added = doc.add_paragraph("Texte inattendu avant la valeur")
        label._element.addnext(added._element)
    doc.save(model)
    target.write_bytes(b"PREVIOUS")
    with pytest.raises(ValueError,match="Modèle"): LimiteAgeService().generate_docx(person,cfg,target,log_generation=False)
    assert target.read_bytes() == b"PREVIOUS"


def test_point_one_document_options_are_deliberately_unchanged(word,person,cfg):
    model,target = word
    cfg.options.ville = "VILLE A TRAITER PLUS TARD"
    cfg.options.signataire_titre = "TITRE A TRAITER PLUS TARD"
    cfg.options.lois_refs = ["REFERENCE A TRAITER PLUS TARD"]
    cfg.options.validite_mois = 12
    LimiteAgeService().generate_docx(person,cfg,target,log_generation=False)
    text = " ".join(p.text for p in Document(target).paragraphs)
    assert "TITRE A TRAITER PLUS TARD" not in text
    assert "REFERENCE A TRAITER PLUS TARD" not in text
    assert "trois (03)" in text
