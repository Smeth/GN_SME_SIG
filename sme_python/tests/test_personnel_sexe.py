"""Normalisation du sexe personnel."""

from sme_gend.models.personnel_sexe import (
    SEXE_F,
    SEXE_M,
    normalize_sexe,
    sexe_display,
    sexe_display_long,
)


def test_normalize_sexe_variants() -> None:
    assert normalize_sexe("M") == SEXE_M
    assert normalize_sexe("féminin") == SEXE_F
    assert normalize_sexe("  F. ") == SEXE_F
    assert normalize_sexe("") is None


def test_sexe_display() -> None:
    assert sexe_display(SEXE_M) == "M"
    assert sexe_display(SEXE_F) == "F"
    assert sexe_display_long(SEXE_F) == "Féminin"
