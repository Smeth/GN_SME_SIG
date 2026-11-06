"""Use the displayed period consistently for advancement inputs and dates."""
from calendar import monthrange
from datetime import date


def avancement_reference_date(settings, *, on: date | None = None) -> date:
    if on is not None:
        return on
    from sme_gend.config.liste_generale_scope import resolve_carriere_scope_mois
    ym = resolve_carriere_scope_mois(settings)
    if not ym:
        return date.today()
    year, month = map(int, ym.split("-"))
    return date(year, month, monthrange(year, month)[1])
