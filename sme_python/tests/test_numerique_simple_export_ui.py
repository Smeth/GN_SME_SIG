"""Ordinary SME export: one chosen month, one workbook, no publication workflow."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication,QDialog,QPushButton

from sme_gend.config.liste_generale_scope import ExportMoisContext
from sme_gend.config.settings import Settings
from sme_gend.ui.views import numerique_combined_view as ui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    template=tmp_path/'SME_GEND_model.xlsx';template.write_bytes(b'SYNTHETIC TEMPLATE')
    settings=Settings(database_path=tmp_path/'unused',template_numerique_path=template,
                       mysql_database='SYNTHETIC_A',mysql_view_mois='2026-09')
    preview=SimpleNamespace(liste_settings=settings,gain_perte_settings=settings)
    view=SimpleNamespace(_settings=settings,_preview=preview)
    state=SimpleNamespace(view=view,settings=settings,template=template,allowed=True,chosen='2026-07',
        cancelled=False,month_hook=lambda:None,file_hook=lambda:None,overwrite_hook=lambda:None,
        destination=str(tmp_path/'chosen.xlsx'),warning='',messages=[],permissions=[])
    class Dialog:
        DialogCode=QDialog.DialogCode
        def __init__(self,base,parent):assert base is settings
        def exec(self):
            state.month_hook()
            return self.DialogCode.Rejected if state.cancelled else self.DialogCode.Accepted
        def chosen_export_mois(self):return state.chosen
    monkeypatch.setattr(ui,'NumeriqueExportDialog',Dialog)
    def require(parent,permission):
        state.permissions.append(permission)
        return state.allowed and permission=='effectifs.export'
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',require)
    def pick(*a,**kw):state.file_hook();return state.destination,''
    monkeypatch.setattr(ui.QFileDialog,'getSaveFileName',pick)
    def overwrite(*a):state.overwrite_hook();return True
    monkeypatch.setattr(ui,'confirm_overwrite_file',overwrite)
    monkeypatch.setattr(ui,'run_with_progress',lambda parent,title,text,work:work())
    def export(base,template,destination,*,export_mois,gain_perte_settings):
        assert base is settings and gain_perte_settings is settings
        Path(destination).write_bytes(b'SYNTHETIC WORKBOOK')
        return SimpleNamespace(path=Path(destination),month=export_mois,journal_warning=state.warning)
    state.export=Mock(side_effect=export)
    monkeypatch.setattr(ui,'export_numerique_workbook',state.export)
    monkeypatch.setattr(ui.QMessageBox,'information',lambda *a:state.messages.append(('info',a[-1])))
    monkeypatch.setattr(ui.QMessageBox,'warning',lambda *a:state.messages.append(('warning',a[-1])))
    return state


@pytest.mark.parametrize('warning',['','Historique indisponible ; fichier conservé.'])
def test_simple_receipt_uses_chosen_month_file_and_only_export_permission(scenario,warning):
    scenario.warning=warning
    ui.NumeriqueCombinedView._export(scenario.view)
    scenario.export.assert_called_once()
    assert scenario.export.call_args.kwargs['export_mois']=='2026-07'
    assert Path(scenario.destination).read_bytes()==b'SYNTHETIC WORKBOOK'
    assert set(scenario.permissions)=={'effectifs.export'}
    assert len(scenario.messages)==1
    kind,message=scenario.messages[0]
    assert kind==('warning' if warning else 'info')
    assert str(scenario.destination) in message and 'juillet 2026' in message.lower()
    assert 'Version :' not in message and 'Dossier conservé' not in message
    assert warning in message


def test_normal_screen_has_no_versions_button_and_does_not_require_publication_rights(app,monkeypatch,scenario):
    monkeypatch.setattr(ui,'build_export_mois_context',lambda settings:ExportMoisContext('2026-09','2026-09',False))
    view=ui.NumeriqueCombinedView(scenario.settings,scenario.view._preview)
    view._loaded=True
    checked=[]
    def permitted(permission):checked.append(permission);return permission in {'effectifs.export','situation.numerique'}
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',permitted)
    view.on_page_activated()
    assert view._btn_export.isEnabled() and view._btn_period_report.isEnabled()
    assert not any('Versions' in button.text() or 'validations' in button.text() for button in view.findChildren(QPushButton))
    assert not any(permission.startswith('publications.') for permission in checked)
    view.close();view.deleteLater();app.processEvents()


@pytest.mark.parametrize('dialog_stage',['month_hook','file_hook','overwrite_hook'])
@pytest.mark.parametrize('change',['permission','context'])
def test_context_or_permission_change_in_any_dialog_prevents_generation(scenario,dialog_stage,change):
    def alter():
        if change=='permission':scenario.allowed=False
        else:scenario.view._preview.liste_settings=replace(scenario.settings,mysql_database='SYNTHETIC_B')
    setattr(scenario,dialog_stage,alter)
    ui.NumeriqueCombinedView._export(scenario.view)
    scenario.export.assert_not_called()
    assert not Path(scenario.destination).exists()
    if change=='context':assert 'contexte courant' in scenario.messages[-1][1]


@pytest.mark.parametrize('stage',['month','file','overwrite'])
def test_cancelled_dialog_does_not_generate_a_workbook(scenario,monkeypatch,stage):
    if stage=='month':scenario.cancelled=True
    if stage=='file':scenario.destination=''
    if stage=='overwrite':monkeypatch.setattr(ui,'confirm_overwrite_file',lambda *a:False)
    ui.NumeriqueCombinedView._export(scenario.view)
    scenario.export.assert_not_called()
    assert not scenario.messages


@pytest.mark.parametrize('month',[None,'2026-13'])
def test_missing_or_invalid_month_is_refused_before_file_generation(scenario,month):
    scenario.chosen=month
    ui.NumeriqueCombinedView._export(scenario.view)
    scenario.export.assert_not_called()
    assert scenario.messages and scenario.messages[-1][0]=='warning'


def test_missing_template_message_names_the_official_model(scenario):
    scenario.template.unlink()
    ui.NumeriqueCombinedView._export(scenario.view)
    scenario.export.assert_not_called()
    assert 'SME_GEND_model.xlsx' in scenario.messages[0][1]
    assert 'numerique.xlsx' not in scenario.messages[0][1]


def test_xlsx_suffix_is_added_before_overwrite_confirmation(scenario,monkeypatch,tmp_path):
    scenario.destination=str(tmp_path/'without_suffix')
    checked=[]
    monkeypatch.setattr(ui,'confirm_overwrite_file',lambda parent,path:checked.append(path) or True)
    ui.NumeriqueCombinedView._export(scenario.view)
    assert checked==[scenario.destination+'.xlsx']
    assert Path(checked[0]).read_bytes()==b'SYNTHETIC WORKBOOK'
