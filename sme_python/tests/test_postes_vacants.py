"""Postes de tête sans titulaire."""

from types import SimpleNamespace

from sme_gend.services.postes_vacants import intitule_poste, lister_postes_tete, lister_postes_vacants


def _structure(code: str, designation: str, ordre: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        designation=designation,
        famille_unite=None,
        ordre_tri=ordre,
    )


def _person(
    structure: str,
    fonction: str | None,
    subdivision: str | None = None,
    noms: str = "AGENT",
) -> SimpleNamespace:
    return SimpleNamespace(
        structure=structure,
        fonction=fonction,
        subdivision_arme=subdivision,
        noms=noms,
    )


def test_cle_exclusion_ignore_casse() -> None:
    from sme_gend.services.postes_tete_exclusions import poste_exclusion_key

    assert poste_exclusion_key(
        "dpf", "Informatique", "Chef de section informatique"
    ) == poste_exclusion_key(
        "DPF", "INFORMATIQUE", "CHEF DE SECTION INFORMATIQUE"
    )


def test_region_sans_commandement_et_brigade_pourvue() -> None:
    rows = lister_postes_vacants(
        [_structure("RG-BZV", "REGION DE GENDARMERIE DE BRAZZAVILLE")],
        [
            ("RG-BZV", "BRIGADE ABSENTE DU MOIS"),
        ],
        [
            _person("RG-BZV", "COMREG"),
            _person("RG-BZV", "COMMANDANT DE BRIGADE", "BRIGADE TERRITORIALE DE MBAMOU"),
            _person("RG-BZV", "ELEVE", "BRIGADE TERRITORIALE DE KINTELE"),
        ],
    )
    postes = {(row.subdivision, row.poste) for row in rows}
    assert ("", "CEMREG") in postes
    assert ("", "COMREG") not in postes
    assert ("BRIGADE TERRITORIALE DE KINTELE", "CB") in postes
    assert ("BRIGADE TERRITORIALE DE MBAMOU", "CB") not in postes
    assert intitule_poste("CB").startswith("CB —")


def test_controle_special_ignore() -> None:
    assert lister_postes_vacants(
        [_structure("CS-DPF", "CONTROLE SPECIAL")],
        [("CS-DPF", "DIVISION ETUDES")],
        [_person("CS-DPF", "ENQUETEUR", "DIVISION ETUDES")],
    ) == []


def test_unite_absente_du_mois_ignoree() -> None:
    rows = lister_postes_vacants(
        [
            _structure("DPF", "DIRECTION DU PERSONNEL ET DE LA FORMATION"),
            _structure("RG-BZV", "REGION DE GENDARMERIE DE BRAZZAVILLE"),
        ],
        [
            ("DPF", "DIVISION FINANCES"),
            ("RG-BZV", "BRIGADE TERRITORIALE DE KINTELE"),
        ],
        [_person("RG-BZV", "COMREG")],
    )
    postes = {(row.structure_code, row.subdivision, row.poste) for row in rows}
    assert ("DPF", "", "DIRECTEUR DPF") not in postes
    assert ("RG-BZV", "BRIGADE TERRITORIALE DE KINTELE", "CB") not in postes
    assert ("RG-BZV", "", "CEMREG") in postes


def test_titulaire_et_effectif() -> None:
    rows = lister_postes_tete(
        [_structure("RG-BZV", "REGION DE GENDARMERIE DE BRAZZAVILLE")],
        [],
        [
            _person("RG-BZV", "COMREG", noms="NGUEMA"),
            _person("RG-BZV", "ELEVE", "SECTION FINANCES", "OKEMBA"),
            _person("RG-BZV", "ELEVE", "SECTION FINANCES", "MOUANGA"),
        ],
    )
    by = {(row.subdivision, row.poste): row for row in rows}
    comreg = by[("", "COMREG")]
    assert comreg.vacant is False
    assert comreg.titulaires == ("NGUEMA",)
    assert comreg.effectif == 3
    section = by[("SECTION FINANCES", "CHEF DE SECTION FINANCES")]
    assert section.vacant is True
    assert section.titulaire_label == "Vacant"
    assert section.type_label == "Section"
    assert section.effectif == 2
