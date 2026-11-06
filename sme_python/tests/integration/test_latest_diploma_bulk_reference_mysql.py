from datetime import date,datetime
from dataclasses import replace
import pytest
from PySide6.QtWidgets import QApplication
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def test_latest_obtention_wins_over_registration_order(isolated_mysql,monkeypatch):
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    dossier.add_diplome(row,categorie='MILITAIRE',libelle='FIRST',date_obtention=date(2026,9,20))
    dossier.add_diplome(personnel.get_by_id('1'),categorie='MILITAIRE',libelle='LAST REGISTERED',date_obtention=date(2026,8,1))
    dossier.add_diplome(personnel.get_by_id('1'),categorie='CIVIL',libelle='CIVIL',date_obtention=date(2026,9,30))
    # Also verify existing snapshots written by the former date-based selector.
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET diplome_libelle='LAST REGISTERED',date_obtention='2026-08-01' WHERE id=1")
    fresh=personnel.get_by_id('1')
    assert fresh.diplome=='FIRST' and fresh.date_obtention=='2026-09-20'
    assert next(r for r in personnel.liste_complete() if r.id=='1').diplome=='FIRST'
    app=QApplication.instance() or QApplication([])
    model=_EditionModel()
    model.load([fresh],None,[],[],[],[],personnel=personnel,settings=settings)
    assert model._rows[0].diplome=='FIRST'
    from sme_gend.services.dossier_read_snapshot import load_dossier_read_snapshot
    snap=load_dossier_read_snapshot(settings,fresh,include_alerts=False)
    assert fresh.diplome=='FIRST'
    app.processEvents()


@pytest.mark.parametrize('reference',[None,'OG 42 / DECRET 2026'])
def test_group_grade_reference_is_saved_in_history_and_current_situation(isolated_mysql,monkeypatch,reference):
    from sme_gend.ui.views.edition_sme_view import _EditionModel
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,date_grade) VALUES(2,2,1,'MDL','2026-08-01')")
    app=QApplication.instance() or QApplication([])
    rows=[personnel.get_by_id(str(i)) for i in (1,2)]
    model=_EditionModel()
    model.load(rows,None,['MDL','ADM'],[],[],[],personnel=personnel,settings=settings)
    count,unchanged,errors=model.bulk_set_grade([0,1],'ADM',datetime(2026,9,1),reference=reference)
    assert (count,unchanged,errors)==(2,0,[])
    for row in rows:
        saved=personnel.get_by_id(row.id)
        assert saved.grade=='ADM'
        assert saved.ndecision==(reference or row.ndecision)
        assert any(e.grade_libelle=='ADM' and e.ndecision==saved.ndecision for e in dossier.list_passations(saved))
    # Same promotion and date with a reference change must also persist.
    count,unchanged,errors=model.bulk_set_grade([0,1],'ADM',datetime(2026,9,1),reference='OG CORRIGE')
    assert (count,unchanged,errors)==(2,0,[])
    assert all(personnel.get_by_id(str(i)).ndecision=='OG CORRIGE' for i in (1,2))
    app.processEvents()


def test_stage_end_saves_diploma_without_hiding_more_recent_obtention(isolated_mysql,monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from sme_gend.ui.views import stages_view as module
    from sme_gend.ui.stage_end_diploma_dialog import StageEndDiplomaDialog
    from sme_gend.models.numerique_satellite import StageDetail
    from sme_gend.repositories.mysql_numerique_repository import MySQLNumeriqueRepository
    settings,connect,params,personnel,dossier=setup_services(isolated_mysql,monkeypatch)
    row=personnel.get_by_id('1')
    dossier.add_diplome(row,categorie='MILITAIRE',libelle='OLD DATE NEWER',date_obtention=date(2026,9,20))
    sat=MySQLNumeriqueRepository(settings)
    sat.upsert_stage('42',StageDetail('FORMATION','TEST','01/09/2026'),type_code='LOCAL',date_debut='2026-09-01')
    row=personnel.get_by_id('1')
    details=sat.load_stages_loc()
    monkeypatch.setattr(StageEndDiplomaDialog,'run',lambda *a,**kw:dict(categorie='MILITAIRE',libelle='STAGE LAST',date_obtention=date(2026,9,15)))
    view=SimpleNamespace(_scoped_settings=settings,_sat=sat,_checked={'local':{'42'}},
        _check_can_edit=lambda **kw:True,_keys_for_start_action=lambda _:['42'],
        _confirm_end_stages=lambda *a:True,_resolve_start_stage_rows=lambda *a:[row],
        _stage_end_reference_date=lambda:'2026-09-15',_prepare_stage_rows=lambda rows:{row.id:row},
        _details_for=lambda _:details,_invalidate_liste_caches=Mock(),
        _view_data=SimpleNamespace(mark_stale=Mock()),_reload_lists_async=Mock())
    module.StagesView._end_checked_stages(view,'local')
    fresh=personnel.get_by_id('1')
    assert fresh.position_admin=='Pr\u00e9sent' and fresh.diplome=='OLD DATE NEWER'
    assert any(d.libelle=='STAGE LAST' and d.date_obtention==date(2026,9,15) for d in dossier.list_diplomes(fresh))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT date_fin FROM parcours_stage WHERE agent_id=1')
            assert cur.fetchone()[0]==date(2026,9,15)


@pytest.mark.parametrize('family',['diplome','grade'])
def test_december_entry_can_take_effect_in_july_without_replacing_later_history(isolated_mysql,monkeypatch,family):
    from test_grade_date_routing_mysql import calendar,period_row
    from sme_gend.services.agent_dossier_service import AgentDossierService
    from sme_gend.services.personnel_service import PersonnelService
    settings,connect,params,personnel,_=calendar(isolated_mysql,monkeypatch,source='2026-12')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(6,'2026-12','2026-12-31','Ouvert')")
            cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,date_grade) VALUES(6,1,6,'MAJ','2026-11-15')")
    dossier=AgentDossierService(settings)
    december=personnel.get_by_id('6')
    if family=='diplome':
        dossier.add_diplome(december,categorie='MILITAIRE',libelle='NOVEMBER',date_obtention=date(2026,11,20))
        dossier.add_diplome(personnel.get_by_id('6'),categorie='MILITAIRE',libelle='JULY',date_obtention=date(2026,7,20))
        assert period_row(personnel,'5').diplome=='JULY'
        assert period_row(personnel,'1').diplome=='JULY'
        assert personnel.get_by_id('6').diplome=='NOVEMBER'
        # Equal qualification dates: the last inserted record wins.
        dossier.add_diplome(personnel.get_by_id('6'),categorie='MILITAIRE',libelle='NOVEMBER LAST',date_obtention=date(2026,11,20))
        assert personnel.get_by_id('6').diplome=='NOVEMBER LAST'
    else:
        personnel.update_personnel(december,replace(december,grade='ADM',date_grade=datetime(2026,7,20)))
        assert period_row(personnel,'5').grade=='ADM'
        assert period_row(personnel,'1').grade=='MDL'
        assert personnel.get_by_id('6').grade=='MAJ'
        assert any(e.grade_libelle=='ADM' and e.date_passation==date(2026,7,20) for e in dossier.list_passations(december))
