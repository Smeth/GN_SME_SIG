"""Filtre du hub structures : type enregistré ou reconnu."""

from sme_gend.config.structure_famille import (
    resolved_structure_famille,
    structure_matches_hub_filter,
)


def test_type_reconnu_sans_famille_enregistree() -> None:
    assert resolved_structure_famille(
        "DPF", "DIRECTION DU PERSONNEL ET DE LA FORMATION", None
    ) == "DIRECTION"
    assert resolved_structure_famille(
        "GGF", "GROUPEMENT DE GENDARMERIE FERROVIAIRE", ""
    ) == "GROUPEMENT"
    assert resolved_structure_famille("EGN", "AUTRE LIBELLE", "ECOLE") == "ECOLE"


def test_filtre_type_et_recherche() -> None:
    dpf = ("DPF", "DIRECTION DU PERSONNEL ET DE LA FORMATION", None)
    ggf = ("GGF", "GROUPEMENT DE GENDARMERIE FERROVIAIRE", None)
    assert structure_matches_hub_filter(*dpf, famille_filter="DIRECTION", query="")
    assert not structure_matches_hub_filter(*ggf, famille_filter="DIRECTION", query="")
    assert structure_matches_hub_filter(*ggf, famille_filter="", query="groupement")
    assert structure_matches_hub_filter(*dpf, famille_filter="", query="personel") is False
    assert structure_matches_hub_filter(
        "RG-BZV",
        "REGION DE GENDARMERIE DE BRAZZAVILLE",
        None,
        famille_filter="REGION",
        query="brazzaville",
    )
