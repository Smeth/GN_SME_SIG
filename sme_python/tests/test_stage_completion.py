from datetime import date
from types import SimpleNamespace as NS
import pytest
from sme_gend.services.stage_completion import validate_stage_end, diploma_display_result


def test_actual_end_date_is_kept_instead_of_month_end():
    assert validate_stage_end('17/09/2026','2026-09',['2026-09-01']) == '2026-09-17'


@pytest.mark.parametrize('value,month,starts', [
    ('','2026-09',[]), ('31/09/2026','2026-09',[]),
    ('2026-10-01','2026-09',[]), ('2026-09-17','2026-09',['2026-09-18'])])
def test_invalid_stage_dates_are_rejected(value,month,starts):
    with pytest.raises(ValueError):
        validate_stage_end(value,month,starts)


def test_future_completion_cannot_mark_personnel_present_early():
    from datetime import timedelta
    future = date.today() + timedelta(days=1)
    with pytest.raises(ValueError, match='future'):
        validate_stage_end(future.isoformat(), future.strftime('%Y-%m'), [])


@pytest.mark.parametrize('category,obtained,selected_id,expected', [
    ('CIVIL', date(2026,9,17), 2, 'civil'),
    ('MILITAIRE', date(2026,10,1), 2, 'postérieure'),
    ('MILITAIRE', date(2026,9,17), 2, 'plus récent'),
    ('MILITAIRE', date(2026,9,17), 1, 'nouveau diplôme affiché'),
])
def test_registration_and_display_are_explained(category,obtained,selected_id,expected):
    payload=dict(categorie=category,date_obtention=obtained)
    selected=NS(categorie='MILITAIRE',date_obtention=date(2026,9,20),id=selected_id,libelle='MILITAIRE RÉCENT')
    assert expected in diploma_display_result(payload,[selected],1,date(2026,9,30))
