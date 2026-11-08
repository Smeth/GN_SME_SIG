"""One export adapter for monthly observations and explicitly dated movements."""


def merge_observed_and_dated(observed, dated):
    # A true fact supplies the detail for this agent/category/month. An Access
    # observation is not an additional occurrence of that same membership change.
    def key(row):
        return str(row.id), 'GAIN' if row.is_gain else 'PERTE' if row.is_perte else str(row.objet)
    represented = {key(row) for row in dated}
    return [row for row in observed if key(row) not in represented] + list(dated)


def export_month_movements(settings, month):
    from sme_gend.repositories.gain_perte_repository import GainPerteRepository
    from sme_gend.services.membership_scope import membership_projection_enabled
    observed = GainPerteRepository(settings).fetch_for_month(month)
    if not membership_projection_enabled(settings):
        return observed
    from sme_gend.services.dated_effectif_movement import export_month_movements as dated_movements
    return merge_observed_and_dated(observed, dated_movements(settings, month))
