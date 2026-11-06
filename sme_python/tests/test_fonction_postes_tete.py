"""Libellés des postes de tête selon le type d'unité."""

from sme_gend.services.fonction_postes_tete import (
    completer_postes_tete,
    poste_index_keys,
    postes_tete_libelles,
    structure_kind,
    subdivision_kind,
)


def test_direction_region_groupement() -> None:
    assert structure_kind("DPF", "DIRECTION DU PERSONNEL ET DE LA FORMATION") == "DIRECTION"
    assert structure_kind("RG-BZV", "REGION DE GENDARMERIE DE BRAZZAVILLE") == "REGION"
    assert structure_kind("GGF", "GROUPEMENT DE GENDARMERIE FERROVIAIRE") == "GROUPEMENT"
    assert structure_kind("CS-DPF", "CONTROLE SPECIAL") is None


def test_libelles_de_tete() -> None:
    assert postes_tete_libelles(
        structure_code="DPF",
        designation="DIRECTION DU PERSONNEL ET DE LA FORMATION",
    ) == ["DIRECTEUR DPF"]
    assert postes_tete_libelles(structure_code="RG-PNR") == ["COMREG", "CEMREG"]
    assert postes_tete_libelles(
        structure_code="1er GGM",
        designation="PREMIER GROUPEMENT DE LA GENDARMERIE MOBILE",
        subdivision="DIVISION LOGISTIQUE",
    ) == ["COMGROUP", "CEMGROUP", "CHEF DE DIVISION LOGISTIQUE"]
    assert subdivision_kind("SECTION FINANCES") == "SECTION"
    assert subdivision_kind("2e SECTION INTERVENTION") == "SECTION"


def test_ecole_centre_cabinet_secretariat() -> None:
    assert structure_kind("EGN", "ECOLE DE LA GENDARMERIE NATIONALE") == "ECOLE"
    assert structure_kind("CIGN", "CENTRE D'INSTRUCTION DE LA GENDARMERIE NATIONALE") == (
        "CENTRE_INSTRUCTION"
    )
    assert structure_kind("CIEGN") == "CENTRE_INSTRUCTION"
    assert structure_kind("CAB-COMGEND", "CABINET DU COMMANDANT DE LA GENDARMERIE") == (
        "CABINET"
    )
    assert structure_kind("GIGN", "GROUPE D'INTERVENTION DE LA GENDARMERIE MOBILE") == (
        "GROUPEMENT"
    )
    assert structure_kind(
        "GE-GQG", "GROUPE D'ESCADRONS DU GRAND QUARTIER GENERAL"
    ) == "GROUPEMENT"
    assert postes_tete_libelles(structure_code="GIGN") == ["COMGROUP", "CEMGROUP"]
    assert structure_kind("COMGEND", "COMMANDEMENT DE LA GENDARMERIE") == "COMMANDEMENT"
    assert postes_tete_libelles(structure_code="COMGEND") == ["COMGEND", "C2 CEMGEND"]
    assert structure_kind("CRO", "CENTRE DE RENSEIGNEMENTS ET D'OPERATIONS") == "CRO"
    assert postes_tete_libelles(structure_code="CRO") == ["CHEF DE CENTRE"]
    assert structure_kind("IT", "INSPECTION TECHNIQUE DE LA GENDARMERIE NATIONALE") == (
        "INSPECTION"
    )
    assert postes_tete_libelles(structure_code="IT") == ["INSPECTEUR IT"]
    assert postes_tete_libelles(structure_code="EGN") == ["COMEC", "CEMEC"]
    assert postes_tete_libelles(
        structure_code="CIK",
        designation="CENTRE D'INSTRUCTION DE KINTELE",
    ) == ["COMCI", "CEMCI"]
    assert postes_tete_libelles(structure_code="CAB-COMGEND") == ["DIRCAB"]
    assert postes_tete_libelles(
        structure_code="DPF",
        designation="DIRECTION DU PERSONNEL ET DE LA FORMATION",
        subdivision="SECRETARIAT",
    ) == ["DIRECTEUR DPF", "CHEF DE SECRETARIAT"]
    assert subdivision_kind("SECRETARIAT") == "SECRETARIAT"
    assert structure_kind("OCR", "OFFICE CENTRAL DU RENSEIGNEMENT") == "OFFICE"
    assert postes_tete_libelles(
        structure_code="OCR",
        designation="OFFICE CENTRAL DU RENSEIGNEMENT",
        subdivision="POLE ANALYSE",
    ) == ["DIRECTEUR OFFICE CENTRAL DU RENSEIGNEMENT", "CHEF DE POLE ANALYSE"]
    assert postes_tete_libelles(
        structure_code="1er GGM",
        designation="PREMIER GROUPEMENT DE LA GENDARMERIE MOBILE",
        subdivision="SERVICE GENERAL",
    ) == ["COMGROUP", "CEMGROUP", "CHEF DE SERVICE GENERAL"]
    assert subdivision_kind("PÔLE RENSEIGNEMENT") == "POLE"
    assert subdivision_kind("COMPAGNIE DE GENDARMERIE TERRITORIALE DE KELLE") == "COMPAGNIE"
    assert subdivision_kind(
        "BRIGADE DE RECHERCHE COMPAGNIE DE GENDARMERIE TERRITORIALE BRAZZAVILLE 1"
    ) == "BRIGADE"
    assert subdivision_kind("PSIG MINDOULI") == "PELOTON"
    assert subdivision_kind("ESSIG") == "ESCADRON"
    assert postes_tete_libelles(
        structure_code="RG-BZV",
        designation="REGION DE GENDARMERIE DE BRAZZAVILLE",
        subdivision="BRIGADE TERRITORIALE DE MBAMOU",
    ) == ["COMREG", "CEMREG", "CB"]
    assert structure_kind(
        "EM-GEND", "ETAT-MAJOR DE LA GENDARMERIE NATIONALE"
    ) == "ETAT_MAJOR"
    assert postes_tete_libelles(structure_code="EM-GEND") == ["C2 CEMGEND"]
    assert subdivision_kind("SECTION DE RECHERCHE JUDICIAIRE") == "SRJ"
    assert subdivision_kind("SRJ BRAZZAVILLE") == "SRJ"
    assert subdivision_kind("DEPARTEMENT DES SPORTS") == "DEPARTEMENT"
    assert postes_tete_libelles(
        structure_code="RG-BZV",
        subdivision="SECTION DE RECHERCHE JUDICIAIRE",
    ) == ["COMREG", "CEMREG", "COM SRJ"]
    assert postes_tete_libelles(
        structure_code="EGN",
        subdivision="DEPARTEMENT DES SPORTS",
        ) == ["COMEC", "CEMEC", "CHEF DE DEPARTEMENT DES SPORTS", "CHEF DEP DES SPORTS"]
    brigade = poste_index_keys(
        "CB",
        structure_code="RG-BZV",
        subdivision="BRIGADE TERRITORIALE DE MBAMOU",
    )
    assert brigade is not None
    assert brigade[0] == "subdivision"
    assert "COMMANDANT DE BRIGADE" in brigade[1]
    srj = poste_index_keys(
        "COMMANDANT DE LA SECTION DE RECHERCHE JUDICIAIRE",
        structure_code="RG-BZV",
        subdivision="SECTION DE RECHERCHE JUDICIAIRE",
    )
    assert srj is not None and "COM SRJ" in srj[1]
    em = poste_index_keys(
        "CHEF D'ETAT MAJOR DE LA GENDARMERIE",
        structure_code="EM-GEND",
    )
    assert em is not None
    assert em[0] == "structure"
    assert "C2 CEMGEND" in em[1]


def test_controle_special_sans_poste_impose() -> None:
    assert completer_postes_tete(
        ["ENQUETEUR"],
        structure_code="CS-DPF",
        designation="CONTROLE SPECIAL",
        subdivision="DIVISION ETUDES",
    ) == ["ENQUETEUR"]
