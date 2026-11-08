from types import SimpleNamespace
import pytest
from openpyxl import load_workbook

from sme_gend.services.operation_progress import progress_reporting


@pytest.mark.parametrize('save_ok',[True,False])
def test_period_export_progress_counts_events_and_waits_for_atomic_save(tmp_path,monkeypatch,save_ok):
    from sme_gend.services import period_activity_report as module
    from openpyxl.workbook.workbook import Workbook
    rows=[dict(date='2026-11-01',agent_id=i,name='Synthetic',kind='Diplôme',detail='Test',observations=1,
               multiple_values=False,source='agent_diplome',source_ids=[i]) for i in range(1001)]
    data=dict(start='2026-11-01',end='2026-11-30',reference_month='2026-11',reference_headcount=1001,
              reference_date='2026-11-30',events=rows,undated_records={'agent_diplome':2})
    monkeypatch.setattr(module,'capture_period_activity',lambda *args:data)
    monkeypatch.setattr(module,'_permissions',lambda:None)
    monkeypatch.setattr('sme_gend.services.sme_publication_backup.storage_root',lambda:tmp_path/'registry')
    monkeypatch.setattr('sme_gend.services.doc_gen_log.log_doc_generation',lambda **kwargs:None)
    events=[]
    save=Workbook.save
    def observed_save(book,path):
        assert events[-1].active_key=='save'
        assert all(event.percent<100 for event in events)
        if not save_ok:raise OSError('synthetic save failure')
        save(book,path)
    monkeypatch.setattr(Workbook,'save',observed_save)
    settings=SimpleNamespace(template_liste_present_path=None,template_numerique_path=None,template_detaches_path=None)
    path=tmp_path/'period.xlsx'
    with progress_reporting(events.append):
        if save_ok:
            assert module.export_period_activity(settings,'2026-11-01','2026-11-30',path) is data
        else:
            with pytest.raises(OSError,match='synthetic save failure'):
                module.export_period_activity(settings,'2026-11-01','2026-11-30',path)
    assert {(event.current,event.total) for event in events}>={(500,1001),(1000,1001),(1001,1001)}
    assert len(events)<30
    if save_ok:
        assert events[-1].percent==100
        book=load_workbook(path)
        assert len(book.sheetnames)==3 and book['Événements datés'].max_row==1002
        book.close()
    else:
        assert not path.exists() and all(event.percent<100 for event in events)
