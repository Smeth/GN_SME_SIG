"""Libellés qualifiés CHEF DE … + subdivision."""

from sme_gend.services.fonction_libelle_qualifier import (
    fonction_matches_qualified_poste,
    qualified_poste_libelle,
    subdivision_qualifier,
)
from sme_gend.services.fonction_postes_tete import postes_tete_libelles


def test_qualifier_division_section() -> None:
    assert subdivision_qualifier("DIVISION GESTION DU PERSONNEL", "DIVISION") == (
        "GESTION DU PERSONNEL"
    )
    assert qualified_poste_libelle("DIVISION GESTION DU PERSONNEL") == (
        "CHEF DE DIVISION GESTION DU PERSONNEL"
    )
    assert qualified_poste_libelle("SECTION DU FICHIER") == "CHEF DE SECTION FICHIER"
    assert qualified_poste_libelle("SECTION FICHIER") == "CHEF DE SECTION FICHIER"


def test_postes_tete_qualifies() -> None:
    libs = postes_tete_libelles(
        structure_code="DPF",
        designation="DIRECTION DU PERSONNEL ET DE LA FORMATION",
        subdivision="DIVISION GESTION DU PERSONNEL",
    )
    assert "DIRECTEUR DPF" in libs
    assert "CHEF DE DIVISION GESTION DU PERSONNEL" in libs


def test_libelle_attendu_reconnait_chef_division_qualifie() -> None:
    sub = "DIVISION GESTION DU PERSONNEL"
    assert fonction_matches_qualified_poste("CHEF DE DIVISION GESTION DU PERSONNEL", sub)
    assert fonction_matches_qualified_poste("CHEF DE DIVISION", sub)
