from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.services.monthly_movements import merge_observed_and_dated


def row(agent, category, date=None):
    return GainPerteRow(id=str(agent),grade='CNE',nom='SYNTHETIC',objet=category,
                        cdsme='112026',observ='',date_reference=date)


def test_real_dates_replace_observation_without_erasing_other_agents_or_events():
    observed=[row(1,'GAIN'),row(2,'PERTE'),row(3,'GAIN')]
    dated=[row(1,'GAIN','2026-11-02'),row(1,'GAIN','2026-11-18'),row(1,'PERTE','2026-11-10')]
    result=merge_observed_and_dated(observed,dated)
    assert result==observed[1:]+dated
