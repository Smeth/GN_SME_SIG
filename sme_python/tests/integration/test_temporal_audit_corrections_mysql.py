"""Read-only production audit: all mutations target disposable synthetic schemas.

Regression coverage for corrected dated saves and field preservation.
"""
from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from test_restore_mysql import isolated_mysql
from test_grade_date_routing_mysql import calendar, period_row
from test_career_alerts_mysql import alert_database
from test_month_fill_mysql import fill_database


def test_grade_respects_segment_end(isolated_mysql, monkeypatch):
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET date_debut='2026-10-01',date_fin='2026-10-10' WHERE id=3")
    row=period_row(personnel,'1')
    personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,10,20)))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT grade_libelle,date_grade,date_fin FROM carriere_situation WHERE id=3')
            assert cur.fetchone()==('MDL',date(2026,8,1),date(2026,10,10))
    print('Corrected path verified.')


def test_control_duplicate_agent_month_is_rejected_by_schema(isolated_mysql,monkeypatch):
    import pymysql
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            with pytest.raises(pymysql.err.IntegrityError,match='uk_carriere_agent_periode'):
                cur.execute("INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle) VALUES(7,1,3,'MDL')")
    print('CONTROL OK: schema prevents two career snapshots for the same agent and period.')


def test_month_fill_applies_future_grade(fill_database,monkeypatch):
    from sme_gend.services import month_fill
    from sme_gend.services.personnel_service import PersonnelService
    from sme_gend.services import sensitive_data_policy, audit_log
    settings,connect,params=fill_database
    monkeypatch.setattr(sensitive_data_policy,'assert_may_edit_personnel_sensitive_fields',lambda *a:None)
    monkeypatch.setattr(audit_log,'append_personnel_audit',lambda **kw:None)
    personnel=PersonnelService(settings)
    row=personnel.get_by_id('1')
    personnel.update_personnel(row,replace(row,grade='ADM',date_grade=datetime(2026,10,1)))
    plan=month_fill.preview_month_fill(settings,'2026-09','2026-10')
    assert not plan.conflicts
    month_fill.apply_month_fill(settings,plan)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT c.grade_libelle FROM carriere_situation c JOIN sme_periode p ON p.id=c.sme_periode_id WHERE c.agent_id=1 AND p.annee_mois='2026-10'")
            assert cur.fetchone()[0]=='ADM'
            cur.execute("SELECT COUNT(*) FROM agent_passation_grade WHERE agent_id=1 AND grade_libelle='ADM' AND date_passation='2026-10-01'")
            assert cur.fetchone()[0]==1
    print('Corrected path verified.')


def test_diploma_date_preserves_details(isolated_mysql,monkeypatch):
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    row=period_row(personnel,'1')
    did=dossier.add_diplome(row,categorie='MILITAIRE',libelle='SYN DIPLOMA',date_obtention=date(2026,8,1),
                          lieu='SYN PLACE',mention='SYN MENTION',etablissement='SYN SCHOOL',obs='SYN NOTES')
    before=period_row(personnel,'1')
    personnel.update_personnel(before,replace(before,date_obtention='2026-09-01'))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT lieu_obtention,mention,etablissement,obs FROM agent_diplome WHERE id=%s',(did,))
            assert cur.fetchone()==('SYN PLACE','SYN MENTION','SYN SCHOOL','SYN NOTES')
    print('Corrected path verified.')


def test_diploma_categories_remain_distinct(isolated_mysql,monkeypatch):
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    row=period_row(personnel,'1')
    military=dossier.add_diplome(row,categorie='MILITAIRE',libelle='SAME LABEL',date_obtention=date(2020,1,1))
    civilian=dossier.add_diplome(row,categorie='CIVIL',libelle='SAME LABEL',date_obtention=date(2026,8,1))
    before=period_row(personnel,'1')
    assert before.date_obtention=='2020-01-01'
    personnel.update_personnel(before,replace(before,date_obtention='2026-09-01'))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT id,categorie,date_obtention FROM agent_diplome WHERE id IN (%s,%s) ORDER BY id',(military,civilian))
            assert cur.fetchall()==((military,'MILITAIRE',date(2026,9,1)),(civilian,'CIVIL',date(2026,8,1)))
    print('Corrected path verified.')


@pytest.mark.parametrize('family',['mission','desertion'])
def test_date_change_moves_event_period(isolated_mysql,monkeypatch,family):
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    events=AgentDossierEventsService(settings)
    if family=='mission':
        values=dict(date_debut='2026-09-10',date_fin=None,nature='SYN',lieu=None,reference_dossier=None,observation=None)
        eid=events.add_mission('42',**values)
        old=next(e for e in events.missions('42') if e.id==eid)
        events.update_mission('42',eid,expected=old,**(values|{'date_debut':'2026-10-01'}))
        table,column='agent_mission','date_debut'
    else:
        eid=events.add_desertion('42',date_desertion='2026-09-10',observation=None)
        old=next(e for e in events.desertions('42') if e.id==eid)
        events.update_desertion('42',eid,expected=old,date_desertion='2026-10-01',observation=None)
        table,column='agent_desertion','date_desertion'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT p.annee_mois,e.{column} FROM {table} e JOIN sme_periode p ON p.id=e.sme_periode_id WHERE e.id=%s',(eid,))
            assert cur.fetchone()==('2026-10',date(2026,10,1))
    print('Corrected path verified.')


def test_legacy_month_retains_cutoff(isolated_mysql,monkeypatch):
    settings,connect,params,personnel,dossier=calendar(isolated_mysql,monkeypatch)
    row=period_row(personnel,'1')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET annee_mois='202609' WHERE id=1")
    dossier.add_diplome(row,categorie='MILITAIRE',libelle='FUTURE OCTOBER',date_obtention=date(2026,10,1))
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT diplome_libelle FROM carriere_situation WHERE id=1')
            assert cur.fetchone()[0] is None
    print('Corrected path verified.')


def test_assignment_uses_effective_month(monkeypatch):
    from sme_gend.ui import personnel_dossier_dialog as module, affectation_edit_dialog
    monkeypatch.setattr('sme_gend.config.sme_context.load_sme_context',lambda:SimpleNamespace(mois_actif='2026-11'))
    dialog=Mock()
    dialog.exec.return_value=QDialog.DialogCode.Accepted
    dialog.values.return_value=dict(structure_code='NEW',fonction=None,subdivision_arme=None,date_affectation=date(2026,8,1))
    monkeypatch.setattr(affectation_edit_dialog,'AffectationEditDialog',lambda *a,**kw:dialog)
    service=Mock()
    row=SimpleNamespace(structure='OLD',fonction=None,subdivision_arme=None)
    view=SimpleNamespace(_row=row,_ref=object(),_dossier_svc=service,
        _settings=SimpleNamespace(mysql_view_mois='2026-09'),_message_fonction_occupee=lambda **kw:None,
        _after_carriere_dossier_mutation=Mock())
    module.PersonnelDossierDialog._on_add_affectation(view)
    assert service.add_affectation.call_args.kwargs['sme_periode_mois']=='2026-08'
    print('Corrected path verified.')


def test_pdf_uses_consulted_month(monkeypatch):
    from sme_gend.ui import personnel_dossier_dialog as module
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *a,**kw:True)
    monkeypatch.setattr('sme_gend.config.sme_context.load_sme_context',lambda:SimpleNamespace(mois_actif='2026-11'))
    picker=Mock(return_value=('', ''))
    monkeypatch.setattr(module.QFileDialog,'getSaveFileName',picker)
    view=SimpleNamespace(_row=SimpleNamespace(noms='SYNTHETIC'),_service=SimpleNamespace(settings=SimpleNamespace(mysql_view_mois='2026-09')))
    module.PersonnelDossierDialog._on_export_parcours_pdf(view)
    assert '202609' in picker.call_args.args[2]
    print('Corrected path verified.')
