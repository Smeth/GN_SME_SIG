"""Shared editors retain their displayed baseline and their draft on conflict."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QLineEdit,QMessageBox
from sme_gend.config.settings import Settings
from sme_gend.models.fonction_priority_config import FonctionPriorityConfig
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.models.limite_age_config import LimiteAgeConfig,GradeLimiteEntry,StructureAttestationOverride
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.repositories.reference_write_guard import ReferenceConflictError
from sme_gend.services.shared_config_write import bind_config_version,copy_config_version,save_shared_model,load_shared_model,VersionedConfigDict


@pytest.fixture
def app(monkeypatch):
    app=QApplication.instance() or QApplication([])
    for name in ('warning','critical','information'):
        monkeypatch.setattr(QMessageBox,name,MagicMock())
    yield app
    app.processEvents()


def editor(kind,monkeypatch):
    settings=Settings(database_path=Path('unused.mdb'))
    service=MagicMock()
    if kind=='priority':
        from sme_gend.ui import fonction_priority_dialog as module
        cfg=FonctionPriorityConfig(fonction_ids=[1]);key='fonction_priority_order'
    elif kind=='detaches':
        from sme_gend.ui.views import detaches_config_view as module
        cfg=DetachesFileConfig();key='detaches_config'
    elif kind=='transmis':
        from sme_gend.ui.views import soit_transmis_view as module
        cfg=SoitTransmisConfig();key='soit_transmis_config'
    else:
        from sme_gend.ui.views import limite_age_view as module
        cfg=LimiteAgeConfig(grades=[GradeLimiteEntry('GND',55,35)])
        cfg.structure_format.overrides=[StructureAttestationOverride('OLD','Old attestation',prep='au')]
        key='limite_age_config'
    bind_config_version(cfg,key,cfg.to_dict(),settings);service.load.return_value=cfg
    if kind=='priority':
        monkeypatch.setattr(module,'ensure_fonction_priority_config',service.load)
        monkeypatch.setattr(module,'save_fonction_priority_config',service.save)
        service.fonctions_all.return_value=[SimpleNamespace(id=1,libelle='F1'),SimpleNamespace(id=2,libelle='F2')]
        view=module.FonctionPriorityDialog(settings,service)
        view._append_item(2,'F2');field=lambda:view._list.count()
        expected=2;save=view._save;reload=view._reload
    elif kind=='detaches':
        monkeypatch.setattr(module,'confirm_yes_no',lambda *a,**kw:True)
        view=module.DetachesConfigView(service);view._lieu.setText('DRAFT')
        field=view._lieu.text;expected='DRAFT';save=view._save;reload=view._load_table_from_disk
    elif kind=='transmis':
        view=module.SoitTransmisView.__new__(module.SoitTransmisView);QWidget.__init__(view)
        view._svc=service
        for name in ('dest_titre','dest_role','ville','ref_num','ref_suffix'):setattr(view,'_f_'+name,QLineEdit())
        view._load_config();view._f_ville.setText('DRAFT')
        field=view._f_ville.text;expected='DRAFT';save=view._save_config;reload=view._load_config
    elif kind=='age_grades':
        service.save = service.save_grades
        view=module._ConfigLimitesTab(settings,service);view._table.item(0,1).setText('58')
        field=lambda:view._table.item(0,1).text();expected='58';save=view._save;reload=view._load_table
    elif kind=='age_options':
        service.save = service.save_doc_options
        view=module._OptionsDocTab(service);view._ville.setText('DRAFT')
        field=view._ville.text;expected='DRAFT';save=view._save;reload=view._load
    else:
        service.save = service.save_structure_format
        monkeypatch.setattr(module._StructureFormatTab,'_fetch_designations',lambda self:{'OLD':'Old'})
        monkeypatch.setattr(module,'sync_structure_format_from_referentiel',lambda st,c:(c.structure_format.overrides,0))
        monkeypatch.setattr(module,'preview_structure_attestation',lambda *a,**kw:'preview')
        view=module._StructureFormatTab(settings,service);view._table.item(0,2).setText('DRAFT')
        field=lambda:view._table.item(0,2).text();expected='DRAFT';save=view._save;reload=view.load_from_disk
    return view,service,cfg,field,expected,save,reload


@pytest.mark.parametrize('kind',['priority','detaches','transmis','age_grades','age_options','age_format'])
@pytest.mark.parametrize('conflict',[False,True])
def test_editor_saves_displayed_version_without_reloading_and_keeps_conflicting_draft(app,monkeypatch,kind,conflict):
    from sme_gend.ui import shared_config_editor
    view,service,cfg,field,expected,save,reload=editor(kind,monkeypatch)
    original=deepcopy(cfg.to_dict());baseline=view._loaded_config;view.accept=MagicMock()
    service.load.reset_mock();service.load.side_effect=AssertionError('Must not reload at save')
    if conflict:service.save.side_effect=ReferenceConflictError('synthetic conflict')
    reported=MagicMock();monkeypatch.setattr(shared_config_editor,'show_reference_conflict',reported)
    save()
    service.save.assert_called_once();service.load.assert_not_called()
    submitted=service.save.call_args.args[1 if kind=='priority' else 0]
    assert submitted is not baseline and submitted.write_token==cfg.write_token and submitted._write_key==cfg._write_key
    assert submitted._write_database==cfg._write_database and submitted.to_dict()!=original
    assert cfg.to_dict()==original and field()==expected
    if conflict:
        assert view._loaded_config is baseline
        if hasattr(view,'_dirty'):assert view._dirty
        view.accept.assert_not_called();reported.assert_called_once()
    else:
        assert view._loaded_config is submitted
        if hasattr(view,'_dirty'):assert not view._dirty
    view.deleteLater()


@pytest.mark.parametrize('kind',['priority','detaches','transmis','age_grades','age_options','age_format'])
def test_failed_reload_preserves_displayed_baseline_and_draft(app,monkeypatch,kind):
    view,service,cfg,field,expected,save,reload=editor(kind,monkeypatch)
    service.load.side_effect=OSError('synthetic offline')
    reload()
    assert view._loaded_config is cfg and field()==expected
    QMessageBox.critical.assert_called_once();service.save.assert_not_called();view.deleteLater()


@pytest.mark.parametrize('kind',['priority','detaches','age','transmis'])
@pytest.mark.parametrize('invalid',['missing','wrong_key','wrong_database'])
def test_shared_model_requires_correct_loaded_version_before_opening_sql(monkeypatch,kind,invalid):
    from sme_gend.services import shared_config_write as module
    settings=Settings(database_path=Path('unused.mdb'))
    model,key={'priority':(FonctionPriorityConfig,'fonction_priority_order'),'detaches':(DetachesFileConfig,'detaches_config'),'age':(LimiteAgeConfig,'limite_age_config'),'transmis':(SoitTransmisConfig,'soit_transmis_config')}[kind]
    cfg=model()
    if invalid!='missing':
        bind_config_version(cfg,key,cfg.to_dict(),settings)
        if invalid=='wrong_key':cfg._write_key='other'
        else:cfg._write_database=('different',)
    monkeypatch.setattr(module,'shared_reference_write',lambda *a,**kw:pytest.fail('Invalid version opened SQL'))
    with pytest.raises(ValueError,match='Version de configuration indisponible'):save_shared_model(settings,key,cfg)


@pytest.mark.parametrize('model',[FonctionPriorityConfig,DetachesFileConfig,LimiteAgeConfig,SoitTransmisConfig])
def test_shared_model_metadata_never_enters_serialized_business_data(model):
    cfg=model();before=cfg.to_dict();cfg.write_token='a'*64;cfg._write_key='private-key';cfg._write_database=('test',)
    cfg._pending_write=object()
    assert cfg.to_dict()==before and cfg==model.from_dict(before) and 'a'*64 not in repr(cfg)
    copied=copy_config_version(cfg,model.from_dict(before))
    assert copied.write_token==cfg.write_token and copied._pending_write is cfg._pending_write and copied._write_database==cfg._write_database


@pytest.mark.parametrize('key',['auth_config','media_storage_config','unknown'])
def test_generic_config_guard_does_not_take_over_operational_or_specialized_configs(key):
    from sme_gend.services.shared_config_write import load_shared_payload
    with pytest.raises(ValueError,match='hors du périmètre'):load_shared_payload(None,key)


def test_failed_initial_load_cannot_build_a_writable_editor_draft(app):
    from sme_gend.ui.shared_config_editor import displayed_config
    view=QWidget()
    with pytest.raises(ValueError,match="n'a pas pu être chargée"):displayed_config(view)
    view.deleteLater()



@pytest.mark.parametrize('key',['fonction_priority_order','detaches_config','limite_age_config','soit_transmis_config','liste_present_layout','postes_tete_exclus','ui_view_state','sme_workspace'])
def test_unversioned_shared_dictionary_cannot_bypass_the_model_guard(monkeypatch,key):
    from sme_gend.services import app_config_store
    monkeypatch.setattr(app_config_store,'_repo',lambda *a,**kw:pytest.fail('Unversioned write opened SQL'))
    with pytest.raises(ValueError,match='Version de configuration indisponible'):
        app_config_store.save_config_dict(None,key,{'synthetic':'value'})


def test_shared_conflict_message_names_the_available_reload_action(app):
    from sme_gend.ui.reference_conflict import show_reference_conflict
    show_reference_conflict(None,ReferenceConflictError('synthetic conflict'),draft=True,reload_available=True)
    message=QMessageBox.warning.call_args.args[2]
    assert 'reste affichée' in message and 'Recharger' in message and 'Annulez' not in message
