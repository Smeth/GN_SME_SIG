from datetime import date,datetime
import pytest
from PySide6.QtWidgets import QApplication
from test_restore_mysql import isolated_mysql
from test_closed_month_corrections_mysql import closed_calendar,snapshots
from test_operator_performance_mysql import install_role
from sme_gend.services.grade_nomination import GradeNomination


@pytest.mark.parametrize('reference',[None,'OG / DECRET 42'])
@pytest.mark.parametrize('stamp',[datetime(2026,7,20),datetime(2027,7,20)])
def test_individual_grade_reference_is_saved_with_nomination(isolated_mysql,monkeypatch,reference,stamp):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_COL_IDX
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    with connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE carriere_situation SET ndecision='OLD OG' WHERE agent_id=1")
            cur.execute("UPDATE agent_passation_grade SET ndecision='OLD OG' WHERE agent_id=1")
    install_role(monkeypatch,True)
    app=QApplication.instance() or QApplication([])
    row=personnel.get_by_id('6')
    model=_EditionModel();model.load([row],None,[],[],[],[],personnel=personnel,settings=settings)
    model.grade_nomination_prompt=lambda *args:GradeNomination(stamp,reference)
    errors=[];model.save_err.connect(errors.append)
    assert model.setData(model.index(0,_COL_IDX['grade']),'ADM'),errors
    fresh=personnel.get_by_id('6')
    if stamp.year==2026:
        assert fresh.grade=='ADM' and fresh.ndecision==reference
        assert all(value==reference for value in snapshots(connect,params,'c.ndecision').values())
    else:
        assert fresh.grade=='MDL' and fresh.ndecision=='OLD OG'
        assert model._rows[0].grade=='MDL' and model._rows[0].ndecision=='OLD OG'
    events=dossier.list_passations(fresh)
    assert any(e.grade_libelle=='ADM' and e.date_passation==stamp.date() and e.ndecision==reference for e in events)
    app.processEvents()


def test_cancelled_individual_nomination_does_not_save_grade_or_reference(isolated_mysql,monkeypatch):
    from sme_gend.ui.views.edition_sme_view import _EditionModel,_COL_IDX
    settings,connect,params,personnel,dossier=closed_calendar(isolated_mysql,monkeypatch)
    install_role(monkeypatch,True)
    app=QApplication.instance() or QApplication([])
    row=personnel.get_by_id('6')
    model=_EditionModel();model.load([row],None,[],[],[],[],personnel=personnel,settings=settings)
    model.grade_nomination_prompt=lambda *args:None
    assert not model.setData(model.index(0,_COL_IDX['grade']),'ADM')
    assert personnel.get_by_id('6').grade=='MDL'
    assert not any(e.grade_libelle=='ADM' for e in dossier.list_passations(row))
    app.processEvents()
