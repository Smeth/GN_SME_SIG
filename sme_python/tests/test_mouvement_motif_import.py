"""Troncature motif / observ lors de l'import gains & pertes Access."""

from sme_gend.services.mysql_access_numerique_import import (
    MOUVEMENT_MOTIF_MAX,
    MOUVEMENT_OBSERV_MAX,
    _clamp_mouvement_payload,
    _normalize_mouvement,
)


def test_normalize_mouvement_long_observ_truncates_motif():
    long_obs = "DECES " + "x" * 200
    cat, motif = _normalize_mouvement("PERTE", long_obs)
    assert cat == "PERTE"
    assert motif == "DECES"
    assert len(motif) <= MOUVEMENT_MOTIF_MAX


def test_normalize_mouvement_gain_defaults_to_ajout():
    cat, motif = _normalize_mouvement("GAIN", None)
    assert cat == "GAIN"
    assert motif == "AJOUT"


def test_clamp_mouvement_payload_preserves_full_observ_up_to_limit():
    long_obs = "y" * 400
    *_, observ_s, _ = _clamp_mouvement_payload(
        objet="PERTE",
        observ=long_obs,
        grade="GND",
        nom="DUPONT",
        cdsme="202605",
        leg="12345",
    )
    assert observ_s is not None
    assert len(observ_s) == MOUVEMENT_OBSERV_MAX
