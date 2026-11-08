import pytest
from sme_gend.services.operation_progress import operation_progress,progress_reporting,current_operation_progress


def test_declared_steps_and_measured_units_drive_monotonic_percentage():
    events=[]
    with progress_reporting(events.append):
        with operation_progress((('backup','Sauvegarde'),('delete','Suppression'))) as progress:
            assert [step.label for step in events[0].steps]==['Sauvegarde','Suppression']
            assert events[0].percent==0
            progress.start('backup',total=4)
            progress.update('backup',current=2,total=4,detail='2 fichiers copiés')
            assert events[-1].percent==25
            progress.finish('backup')
            progress.start('delete',total=100)
            progress.update('delete',current=30,total=100)
            assert events[-1].percent==65
            progress.update('delete',detail='Attente de la validation MySQL…')
            assert events[-1].percent==65
            progress.finish('delete')
    assert [event.percent for event in events]==sorted(event.percent for event in events)
    assert events[-1].percent==100 and current_operation_progress() is None


def test_two_nested_months_each_contribute_their_actual_share():
    events=[]
    with progress_reporting(events.append),operation_progress((('restore','Restaurer les mois'),('commit','Valider'))) as parent:
        parent.start('restore')
        for index in range(2):
            with parent.subtask('restore',current=index,total=2):
                with operation_progress((('read','Lecture'),('rows','Écriture'))) as child:
                    child.start('read');child.finish('read')
                    child.start('rows');child.finish('rows')
            assert events[-1].percent==(index+1)*25
        parent.finish('restore');parent.start('commit')
        assert events[-1].percent==50
        parent.finish('commit')
    assert all([step.label for step in item.steps]==['Restaurer les mois','Valider'] for item in events)
    assert all(a.percent<=b.percent for a,b in zip(events,events[1:]))


def test_failed_work_does_not_finish_the_active_or_remaining_phases():
    events=[]
    with progress_reporting(events.append),pytest.raises(RuntimeError):
        with operation_progress((('read','Lecture'),('write','Écriture'),('commit','Validation'))) as progress:
            progress.start('read');progress.finish('read');progress.start('write')
            raise RuntimeError('synthetic failure')
    assert events[-1].percent<100
    assert events[-1].steps[-1].state=='pending' and current_operation_progress() is None


def test_a_broken_progress_observer_cannot_abort_a_business_operation():
    def failed(snapshot):raise RuntimeError('synthetic observer failure')
    with progress_reporting(failed),operation_progress((('write','Écriture'),)) as progress:
        progress.start('write');progress.update('write',current=1,total=1);progress.finish('write')


def test_fractional_table_units_preserve_the_current_table_progress():
    events=[]
    with progress_reporting(events.append),operation_progress((('sql','Données'),)) as progress:
        progress.start('sql',total=10)
        progress.update('sql',current=2.5,total=10,detail='Table 3 : 500 / 1000 lignes')
    assert events[-1].percent==25


def test_nested_completion_keeps_parent_running_until_its_controls_finish():
    events=[]
    with progress_reporting(events.append),operation_progress((('backup','Sauvegarde et contrôle'),)) as parent:
        parent.start('backup')
        with operation_progress((('write','Écriture'),)) as child:
            child.start('write');child.finish('write')
        assert events[-1].percent==99 and events[-1].steps[0].state=='running'
        parent.update('backup',detail='Contrôle de l’archive')
        assert events[-1].steps[0].state=='running'
        parent.finish('backup')
    assert events[-1].percent==100 and events[-1].steps[0].state=='done'
