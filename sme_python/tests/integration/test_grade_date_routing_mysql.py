from dataclasses import replace
from datetime import date, datetime
import pytest
from PySide6.QtWidgets import QApplication
from test_restore_mysql import isolated_mysql
from test_sme_stage_persistence_mysql import setup_services


def period_row(personnel, row_id):
    from sme_gend.services.personnel_service import PersonnelService
    month = {'1':'2026-09','2':'2026-08','3':'2026-10','4':'2026-11','5':'2026-07'}[str(row_id)]
    return PersonnelService(replace(personnel.settings, mysql_view_mois=month)).get_by_id(str(row_id))


def calendar(isolated_mysql, monkeypatch, source='2026-09', closed=None, missing_october=False):
    settings, connect, params, personnel, dossier = setup_services(isolated_mysql, monkeypatch)
    settings = replace(settings, mysql_view_mois=source)
    monkeypatch.setattr('sme_gend.repositories.mysql_agent_repository.resolve_carriere_scope_mois', lambda s: s.mysql_view_mois)
    from sme_gend.services.personnel_service import PersonnelService
    personnel = PersonnelService(settings)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ouvert' WHERE id=1")
            cur.execute("UPDATE carriere_situation SET grade_libelle='MDL', date_grade='2026-08-01' WHERE id=1")
            for pid, month, grade, stamp in [(2,'2026-08','MDL','2026-08-01'), (3,'2026-10','MDL','2026-08-01'),
                                            (4,'2026-11','MAJ','2026-11-15'), (5,'2026-07','LT','2026-07-01')]:
                if missing_october and month == '2026-10': continue
                cur.execute('INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(%s,%s,LAST_DAY(CONCAT(%s,"-01")),%s)',
                            (pid, month, month, 'Fermé' if month == closed else 'Ouvert'))
                cur.execute('INSERT INTO carriere_situation(id,agent_id,sme_periode_id,grade_libelle,date_grade) VALUES(%s,1,%s,%s,%s)',
                            (pid,pid,grade,stamp))
            if closed == '2026-09': cur.execute("UPDATE sme_periode SET statut='Fermé' WHERE id=1")
            cur.execute("INSERT INTO agent_passation_grade(agent_id,grade_libelle,date_passation) VALUES(1,'LT','2026-07-01'),(1,'MDL','2026-08-01'),(1,'MAJ','2026-11-15')")
    return settings, connect, params, personnel, dossier


@pytest.mark.parametrize('source,stamp,expected', [
    ('2026-09', datetime(2026,10,1), {1:'MDL',2:'MDL',3:'ADM',4:'MAJ',5:'LT'}),
    ('2026-11', datetime(2026,8,20), {1:'ADM',2:'ADM',3:'ADM',4:'MAJ',5:'LT'}),
])
def test_edit_routes_by_nomination_date_and_reloads_current_month(isolated_mysql, monkeypatch, source, stamp, expected):
    from sme_gend.ui.views.edition_sme_view import _EditionModel, _COL_IDX
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch, source)
    source_id = '1' if source == '2026-09' else '4'
    app = QApplication.instance() or QApplication([])
    row = period_row(personnel, source_id)
    model = _EditionModel()
    model.load([row], None, [], [], [], [], personnel=personnel, settings=settings)
    model.grade_nomination_prompt = lambda *a: stamp
    assert model.setData(model.index(0,_COL_IDX['grade']), 'ADM')
    for rid, grade in expected.items(): assert period_row(personnel, str(rid)).grade == grade
    assert model._rows[0].grade == expected[int(source_id)]
    assert any(p.grade_libelle=='ADM' and p.date_passation==stamp.date() for p in dossier.list_passations(row))
    assert model.active_undo_count() == 0
    app.processEvents()


def test_closed_months_are_corrected_without_reopening(isolated_mysql, monkeypatch):
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch, source='2026-11', closed='2026-08')
    row = period_row(personnel, '4')
    personnel.update_personnel(row, replace(row,grade='ADM',date_grade=datetime(2026,8,20)))
    assert period_row(personnel, '2').grade == 'ADM'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE sme_periode SET statut='Ferm\u00e9' WHERE id=1")
    personnel.update_personnel(period_row(personnel, '4'), replace(row,grade='ADC',date_grade=datetime(2026,8,20)))
    assert period_row(personnel, '2').grade == 'ADC'
    assert period_row(personnel, '1').grade == 'ADC'
    assert period_row(personnel, '3').grade == 'ADC'
    assert period_row(personnel, '4').grade == 'MAJ'
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT statut FROM sme_periode WHERE id IN (1,2)')
            assert all(r[0]=='Ferm\u00e9' for r in cur.fetchall())


def test_future_event_is_kept_without_inventing_missing_monthly_snapshot(isolated_mysql, monkeypatch):
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch, missing_october=True)
    row = period_row(personnel, '1')
    personnel.update_personnel(row, replace(row,grade='ADM',date_grade=datetime(2026,10,1)))
    personnel.update_personnel(period_row(personnel, '1'), replace(row,grade='ADM',date_grade=datetime(2026,10,1)))
    assert period_row(personnel, '1').grade == 'MDL'
    assert any(p.grade_libelle=='ADM' and p.date_passation==date(2026,10,1) for p in dossier.list_passations(row))
    assert sum(p.grade_libelle=='ADM' for p in dossier.list_passations(row)) == 1
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM carriere_situation WHERE agent_id=1')
            assert cur.fetchone()[0] == 4


def test_legacy_closed_month_label_allows_dated_correction(isolated_mysql, monkeypatch):
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch, source='2026-11')
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sme_periode(id,annee_mois,date_dernier_jour_mois,statut) VALUES(6,'202608','2026-08-31','Fermé')")
    row = period_row(personnel, '4')
    personnel.update_personnel(row, replace(row,grade='ADM',date_grade=datetime(2026,8,20)))
    assert any(p.grade_libelle=='ADM' for p in dossier.list_passations(row))
    assert period_row(personnel,'2').grade=='ADM'


def test_dossier_history_add_update_delete_reprojects_affected_months(isolated_mysql, monkeypatch):
    settings, connect, params, personnel, dossier = calendar(isolated_mysql, monkeypatch)
    row = period_row(personnel, '1')
    event_id = dossier.add_passation(row, grade_libelle='ADM', date_passation=date(2026,10,1))
    assert period_row(personnel, '1').grade == 'MDL'
    assert period_row(personnel, '3').grade == 'ADM'
    event = next(p for p in dossier.list_passations(row) if p.id == event_id)
    dossier.update_passation(row, event_id, expected=event, grade_libelle='ADM', date_passation=date(2026,8,20))
    assert period_row(personnel, '1').grade == 'ADM'
    assert period_row(personnel, '4').grade == 'MAJ'
    event = next(p for p in dossier.list_passations(row) if p.id == event_id)
    dossier.delete_passation(period_row(personnel, '1'), event_id, expected=event)
    assert period_row(personnel, '1').grade == 'MDL'
    assert period_row(personnel, '3').grade == 'MDL'
