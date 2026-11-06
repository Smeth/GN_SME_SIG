from sme_gend.services.fonction_structure_choices import (
    fonction_acceptable_for_assignment,
    canonical_fonction_libelle,
    merge_fonction_suggestions,
)


def test_poste_qualifie_accepte_sans_lien_referentiel() -> None:
    sub = "DIVISION GESTION DU PERSONNEL"
    lib = "CHEF DE DIVISION GESTION DU PERSONNEL"
    assert fonction_acceptable_for_assignment(
        lib,
        linked_libelles=["CHEF_DGP"],
        structure_code="DPF",
        designation="DIRECTION DU PERSONNEL ET DE LA FORMATION",
        famille="DIRECTION",
        subdivision=sub,
    )
    assert canonical_fonction_libelle(
        lib,
        structure_code="DPF",
        designation="DIRECTION DU PERSONNEL ET DE LA FORMATION",
        famille="DIRECTION",
        subdivision=sub,
    ) == lib


def test_suggestions_edition_mettent_les_fonctions_deja_portees_en_tete() -> None:
    choices = merge_fonction_suggestions(
        current="COM_EC",
        used_same_subdivision=["COM_EC", "ADJ_COM_EC"],
        used_same_structure=["CHEF_DIV_INFORMATIQUE"],
        linked_and_expected=["COM_EGN", "COMEC", "CEMEC"],
    )
    assert choices[:4] == ["COM_EC", "ADJ_COM_EC", "CHEF_DIV_INFORMATIQUE", "COM_EGN"]
    assert "COMEC" in choices
    assert choices.count("COM_EC") == 1
