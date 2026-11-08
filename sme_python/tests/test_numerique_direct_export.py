"""Direct export permissions, isolation and failure handling without publications."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openpyxl import Workbook
import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.detaches_file_config import DetachesFileConfig
from sme_gend.repositories.mysql_connection import CommittedMirrorError
from sme_gend.services import auth_guard
from sme_gend.services import numerique_excel_export as export


@pytest.fixture
def direct_export(tmp_path,monkeypatch):
    template=tmp_path/'synthetic_model.xlsx'
    workbook=Workbook();sheet=workbook.active;sheet.title='NUMERIQUE'
    sheet['B7']=' CNE ';sheet['C7']='TOTAL'
    workbook.save(template);workbook.close()
    settings=Settings(database_path=tmp_path/'unused',mysql_host='source.invalid',
                      mysql_database='synthetic_source',mysql_view_mois='2026-09')
    requested=[];allowed={'effectifs.export'}
    def has_permission(permission):
        requested.append(permission)
        return permission in allowed
    monkeypatch.setattr(auth_guard,'_session_has',has_permission)
    forbidden=Mock(side_effect=AssertionError('Direct export must not require a migration or publication'))
    monkeypatch.setattr('sme_gend.services.schema_migrations.require_capability',forbidden)
    monkeypatch.setattr('sme_gend.services.sme_publication_registry.register_export',forbidden)
    monkeypatch.setattr(export,'_capture_publication_inputs',forbidden)
    archive_root=tmp_path/'protected_archive'
    monkeypatch.setattr('sme_gend.services.sme_export_revision.revision_root',lambda settings:archive_root/'source')
    monkeypatch.setattr('sme_gend.services.sme_publication_backup.storage_root',lambda:archive_root)
    def capture(settings,month,grade_order,movement_settings,*,include_revision):
        assert include_revision is False and grade_order==['CNE']
        assert settings.mysql_view_mois==movement_settings.mysql_view_mois==month
        data={'mois':month,'rows':[SimpleNamespace(id='1',noms='SYNTHETIC AGENT')],
              'grades':[],'details':{},'detaches':[],'previous_counts':[None]*10,'movements':[],
              'detaches_config':DetachesFileConfig().to_dict()}
        return data,{'captured_at_utc':'2026-11-08T10:00:00+00:00'}
    capture_mock=Mock(side_effect=capture)
    monkeypatch.setattr(export,'_capture_numerique_workbook_data',capture_mock)
    def render(model,destination,**values):
        assert model.getvalue()==template.read_bytes()
        assert isinstance(values['detaches_config'],DetachesFileConfig)
        Path(destination).write_bytes(b'SYNTHETIC COMPLETED WORKBOOK')
        return ('NUMERIQUE','SYNTHETIC HIDDEN SHEET')
    render_mock=Mock(side_effect=render)
    monkeypatch.setattr('sme_gend.services.numerique_model_export.build_from_model',render_mock)
    journal=Mock()
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',journal)
    return SimpleNamespace(settings=settings,template=template,destination=tmp_path/'output.xlsx',
        requested=requested,allowed=allowed,forbidden=forbidden,capture=capture_mock,render=render_mock,
        journal=journal,archive_root=archive_root)


def test_effectifs_export_alone_generates_the_chosen_month_without_publication(direct_export):
    case=direct_export
    assert not auth_guard.session_has_permission('publications.create')
    case.requested.clear()
    model_hash=sha256(case.template.read_bytes()).hexdigest()
    result=export.export_numerique_workbook(case.settings,case.template,case.destination,
        export_mois='2025-06',mois_actif='2026-11')
    assert result.path==case.destination and result.month=='2025-06' and result.journal_warning==''
    assert case.requested==['effectifs.export']
    case.forbidden.assert_not_called()
    assert case.destination.read_bytes()==b'SYNTHETIC COMPLETED WORKBOOK'
    assert sha256(case.template.read_bytes()).hexdigest()==model_hash
    assert not case.archive_root.exists()
    log=case.journal.call_args.kwargs
    assert log['mois']=='2025-06' and log['settings'].mysql_view_mois=='2025-06'
    assert log['meta']['export_mode']=='direct' and log['meta']['template_sha256']==model_hash
    assert log['meta']['sheets']==['NUMERIQUE','SYNTHETIC HIDDEN SHEET']
    assert not any(key in log['meta'] for key in ('publication_id','revision_id','revision_no'))


def test_export_permission_denial_precedes_capture_and_output(direct_export):
    case=direct_export;case.allowed.clear()
    with pytest.raises(PermissionError,match='exporter'):
        export.export_numerique_workbook(case.settings,case.template,case.destination)
    case.capture.assert_not_called();case.render.assert_not_called();case.journal.assert_not_called()
    case.forbidden.assert_not_called()
    assert not case.destination.exists()


@pytest.mark.parametrize('failure',[OSError('SYNTHETIC JOURNAL FAILURE'),CommittedMirrorError('SYNTHETIC MIRROR FAILURE')])
def test_journal_failure_returns_a_warning_and_keeps_the_completed_file(direct_export,failure):
    case=direct_export;case.journal.side_effect=failure
    result=export.export_numerique_workbook(case.settings,case.template,case.destination)
    assert result.path==case.destination and result.month=='2026-09'
    assert case.destination.read_bytes()==b'SYNTHETIC COMPLETED WORKBOOK'
    assert 'fichier Excel est enregistré et utilisable' in result.journal_warning
    if isinstance(failure,CommittedMirrorError):
        assert 'Historique confirmé dans MySQL' in result.journal_warning
    else:
        assert "n'a pas pu être confirmé" in result.journal_warning
    case.render.assert_called_once();case.journal.assert_called_once();case.forbidden.assert_not_called()


@pytest.mark.parametrize('month',['2026-00','2026-13','invalid',None])
def test_invalid_or_missing_month_cannot_replace_an_existing_output(direct_export,month):
    case=direct_export;case.destination.write_bytes(b'SYNTHETIC PREVIOUS WORKBOOK')
    settings=replace(case.settings,mysql_view_mois=None)
    with pytest.raises(ValueError,match='mois SME'):
        export.export_numerique_workbook(settings,case.template,case.destination,export_mois=month)
    case.capture.assert_not_called();case.render.assert_not_called();case.journal.assert_not_called()
    assert case.destination.read_bytes()==b'SYNTHETIC PREVIOUS WORKBOOK'


@pytest.mark.parametrize('override',[{'mysql_host':'other.invalid'},{'mysql_port':33330},{'mysql_database':'other_source'}])
def test_effectifs_and_movements_must_come_from_the_same_database(direct_export,override):
    case=direct_export;case.destination.write_bytes(b'SYNTHETIC PREVIOUS WORKBOOK')
    with pytest.raises(ValueError,match='même base'):
        export.export_numerique_workbook(case.settings,case.template,case.destination,
            gain_perte_settings=replace(case.settings,**override))
    case.capture.assert_not_called();case.render.assert_not_called();case.journal.assert_not_called()
    assert case.destination.read_bytes()==b'SYNTHETIC PREVIOUS WORKBOOK'


def test_export_cannot_overwrite_the_model(direct_export):
    case=direct_export;before=case.template.read_bytes()
    with pytest.raises(ValueError,match='modèle SME'):
        export.export_numerique_workbook(case.settings,case.template,case.template)
    case.capture.assert_not_called();case.render.assert_not_called();case.journal.assert_not_called()
    assert case.template.read_bytes()==before


def test_failed_capture_keeps_the_previous_output_without_generation_or_journal(direct_export):
    case=direct_export;case.destination.write_bytes(b'SYNTHETIC PREVIOUS WORKBOOK')
    failure=OSError('SYNTHETIC SNAPSHOT FAILURE');case.capture.side_effect=failure
    with pytest.raises(OSError) as caught:
        export.export_numerique_workbook(case.settings,case.template,case.destination)
    assert caught.value is failure
    case.render.assert_not_called();case.journal.assert_not_called();case.forbidden.assert_not_called()
    assert case.destination.read_bytes()==b'SYNTHETIC PREVIOUS WORKBOOK'
