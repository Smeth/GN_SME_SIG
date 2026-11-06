from dataclasses import fields
import pytest
from test_restore_mysql import isolated_mysql
from test_career_alerts_mysql import alert_database


def test_compound_name_boundary_is_shared_versioned_and_publishes_notification(alert_database):
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.services.limite_age_service import LimiteAgeService
    from sme_gend.services.person_name_display import format_person_name
    from sme_gend.repositories.reference_write_guard import ReferenceConflictError
    from sme_gend.services.data_sync import read_data_revision
    settings, connect, params = alert_database
    person = ListeGeneraleRow(**({f.name: None for f in fields(ListeGeneraleRow)} |
        dict(id='1',access_join_id='101',noms='MBOUNGOU MAKITA JEAN PAUL')))
    first, second = LimiteAgeService(), LimiteAgeService()
    loaded_a, loaded_b = first.load(settings), second.load(settings)
    before = read_data_revision(settings)
    first.save_name_format(loaded_a,person,'MBOUNGOU MAKITA','Jean Paul',settings)
    assert read_data_revision(settings) != before
    assert format_person_name(person,second.load(settings)) == 'MBOUNGOU MAKITA Jean Paul'
    with pytest.raises(ReferenceConflictError):
        second.save_name_format(loaded_b,person,'MBOUNGOU','Makita Jean Paul',settings)
    assert format_person_name(person,first.load(settings)) == 'MBOUNGOU MAKITA Jean Paul'
