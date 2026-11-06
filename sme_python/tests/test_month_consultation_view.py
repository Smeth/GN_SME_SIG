"""Consultation d'un mois importé : filtre liste / tableau de bord."""

from sme_gend.config.liste_generale_scope import should_filter_liste_for_display_mois


def test_consultation_applies_view_filter_even_if_month_not_closed() -> None:
    assert should_filter_liste_for_display_mois(
        "2026-04",
        operational_mois="2026-05",
        consultation_mois="2026-04",
        month_is_closed=False,
    )


def test_operational_month_no_filter_when_not_consulting() -> None:
    assert not should_filter_liste_for_display_mois(
        "2026-05",
        operational_mois="2026-05",
        consultation_mois=None,
        month_is_closed=False,
    )


def test_closed_month_always_filtered() -> None:
    assert should_filter_liste_for_display_mois(
        "2026-03",
        operational_mois="2026-05",
        consultation_mois=None,
        month_is_closed=True,
    )
