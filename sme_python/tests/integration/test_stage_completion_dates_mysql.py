"""Real completion dates and diploma feedback on a disposable MySQL only."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QMessageBox
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


@pytest.mark.parametrize('kind', ['local','ex','interne'])
def test_actual_stage_end_is_independent_of_diploma_date(isolated_mysql, monkeypatch, kind):
    from test_sme_stage_persistence_mysql import test_stage_end_and_diploma_commit_together
    test_stage_end_and_diploma_commit_together(isolated_mysql,monkeypatch,kind,False,False,actual_end='2026-09-17')
    assert '2026-09-17' in QMessageBox.information.call_args.args[2]
    assert 'nouveau diplôme affiché' in QMessageBox.information.call_args.args[2]


@pytest.mark.parametrize('category,stamp,expected,explanation', [
    ('CIVIL',date(2026,9,20),'OLD MILITARY','civil'),
    ('MILITAIRE',date(2026,9,10),'OLD MILITARY','plus récent'),
    ('MILITAIRE',date(2026,10,1),'OLD MILITARY','postérieure'),
])
def test_diploma_is_saved_and_nonselection_is_explicit(isolated_mysql,monkeypatch,category,stamp,expected,explanation):
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    dossier.add_diplome(row,categorie='MILITAIRE',libelle='OLD MILITARY',date_obtention=date(2026,9,15))
    sat=MySQLNumeriqueRepository(settings)
    sat.upsert_stage('42',StageDetail('FORMATION','TEST','01/09/2026'),type_code='LOCAL',date_debut='2026-09-01')
    row=personnel.get_by_id('1')
    details=sat.load_stages_loc()
    monkeypatch.setattr(StageEndDiplomaDialog,'run',lambda *a,**kw:dict(categorie=category,libelle='NEW',
        date_obtention=stamp,_stage_end_date='2026-09-17'))
    view=SimpleNamespace(_scoped_settings=settings,_sat=sat,_checked={'local':{'42'}},
        _check_can_edit=lambda **kw:True,_keys_for_start_action=lambda _:['42'],
        _confirm_end_stages=lambda *a:True,_resolve_start_stage_rows=lambda *a:[row],
        _stage_end_reference_date=lambda:'2026-09-30',_prepare_stage_rows=lambda rows:{row.id:row},
        _details_for=lambda _:details,_invalidate_liste_caches=Mock(),
        _view_data=SimpleNamespace(mark_stale=Mock()),_reload_lists_async=Mock())
    module.StagesView._end_checked_stages(view,'local')
    fresh=personnel.get_by_id('1')
    assert fresh.position_admin=='Présent' and fresh.diplome==expected
    assert any(d.libelle=='NEW' and d.date_obtention==stamp for d in dossier.list_diplomes(fresh))
    assert explanation in QMessageBox.information.call_args.args[2]


@pytest.mark.parametrize('kind',['local','ex','interne'])
def test_repeating_end_stage_with_fresh_history_does_not_duplicate_diploma(isolated_mysql,monkeypatch,kind):
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    sat=MySQLNumeriqueRepository(settings)
    code=module._stage_type_code(kind)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO ref_type_stage(code,libelle) VALUES('INTERNE','Interne')")
    sat.upsert_stage('42',StageDetail('FORMATION','TEST','01/09/2026'),type_code=code,date_debut='2026-09-01')
    monkeypatch.setattr(StageEndDiplomaDialog,'run',lambda *a,**kw:dict(categorie='MILITAIRE',libelle='STAGE RESULT',
        date_obtention=date(2026,9,17),_stage_end_date='2026-09-17'))
    for _ in range(2):
        row=personnel.get_by_id('1')
        details={'local':sat.load_stages_loc,'ex':sat.load_stages_ex,'interne':sat.load_stages_internal}[kind]()
        view=SimpleNamespace(_scoped_settings=settings,_sat=sat,_checked={kind:{'42'}},
            _check_can_edit=lambda **kw:True,_keys_for_start_action=lambda _:['42'],
            _confirm_end_stages=lambda *a:True,_resolve_start_stage_rows=lambda *a:[row],
            _stage_end_reference_date=lambda:'2026-09-30',_prepare_stage_rows=lambda rows:{row.id:row},
            _details_for=lambda _:details,_invalidate_liste_caches=Mock(),
            _view_data=SimpleNamespace(mark_stale=Mock()),_reload_lists_async=Mock())
        module.StagesView._end_checked_stages(view,kind)
    assert len(dossier.list_diplomes(personnel.get_by_id('1')))==1


def test_stage_postcommit_mirror_failure_is_reported_as_committed(isolated_mysql,monkeypatch):
    from sme_gend.services.agent_dossier_service import AgentDossierService
    from sme_gend.repositories.mysql_connection import after_mysql_commit
    from test_sme_stage_persistence_mysql import test_stage_end_and_diploma_commit_together
    original=AgentDossierService._log_history
    def log(self,*args,**kwargs):
        original(self,*args,**kwargs)
        def fail():
            raise OSError('SYNTHETIC local mirror failure')
        after_mysql_commit(fail)
    monkeypatch.setattr(AgentDossierService,'_log_history',log)
    test_stage_end_and_diploma_commit_together(isolated_mysql,monkeypatch,'local',False,False)
    message=QMessageBox.information.call_args.args[2]
    assert 'confirmé dans MySQL' in message
    assert 'aucune modification' not in message
