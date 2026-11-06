"""Une fonction de structure ne peut être tenue que par une personne."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from sme_gend.services.fonction_occupancy import (
    affectation_devient_poste_actuel,
    assert_fonction_disponible,
    fonction_choice_states,
    occupancy_message,
    occupants_by_fonction,
    other_occupants,
    same_fonction_poste,
)


def _row(
    row_id: str,
    structure: str,
    fonction: str | None,
    noms: str = "AGENT",
    subdivision: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        noms=noms,
        structure=structure,
        fonction=fonction,
        subdivision_arme=subdivision,
    )


def test_meme_poste_ignore_casse_et_accents() -> None:
    assert same_fonction_poste("COMGEND", "Directeur DSF", "comgend", "DIRECTEUR DSF")
    assert not same_fonction_poste("COMGEND", "DIRECTEUR DSF", "DPF", "DIRECTEUR DSF")


def test_fonction_deja_tenue_sur_la_structure() -> None:
    rows = [
        _row("1", "COMGEND", "DIRECTEUR DSF", "GALIBAYI Roch"),
        _row("2", "DPF", "DIRECTEUR DSF", "AUTRE"),
        _row("3", "COMGEND", "Chauffeur", "NGOMA"),
    ]
    found = other_occupants(
        rows, structure="COMGEND", fonction="directeur dsf", exclude_row_id="9"
    )
    assert [o.noms for o in found] == ["GALIBAYI Roch"]
    with pytest.raises(ValueError, match="GALIBAYI Roch"):
        assert_fonction_disponible(
            rows, structure="COMGEND", fonction="DIRECTEUR DSF", exclude_row_id="9"
        )


def test_meme_fonction_dans_une_autre_subdivision() -> None:
    rows = [
        _row("1", "RG-BZV", "COMMANDANT", "A", "BRIGADE TERRITORIALE DE MBAMOU"),
        _row("2", "RG-BZV", "COMMANDANT", "B", "BRIGADE TERRITORIALE DE KINTELE"),
    ]
    assert other_occupants(
        rows,
        structure="RG-BZV",
        fonction="COMMANDANT",
        subdivision="BRIGADE TERRITORIALE DE KINTELE",
        exclude_row_id="2",
    ) == []
    found = other_occupants(
        rows,
        structure="RG-BZV",
        fonction="COMMANDANT",
        subdivision="BRIGADE TERRITORIALE DE MBAMOU",
        exclude_row_id="9",
    )
    assert [o.noms for o in found] == ["A"]
    assert "MBAMOU" in occupancy_message(found, "COMMANDANT")


def test_directeur_unique_pour_toute_la_direction() -> None:
    rows = [
        _row("1", "DPF", "DIRECTEUR DPF", "A"),
        _row("2", "DPF", "DIRECTEUR", "B", "DIVISION GESTION DU PERSONNEL"),
    ]
    found = other_occupants(
        rows,
        structure="DPF",
        fonction="DIRECTEUR DPF",
        subdivision="SECTION FINANCES",
        exclude_row_id="9",
    )
    assert {o.noms for o in found} == {"A", "B"}


def test_comreg_et_cemreg_uniques_dans_la_region() -> None:
    rows = [
        _row("1", "RG-BZV", "COMREG_RGBZV", "A", "ETAT-MAJOR"),
        _row("2", "RG-PNR", "CEMREG", "B"),
    ]
    found = other_occupants(
        rows, structure="RG-BZV", fonction="COMREG", exclude_row_id="9"
    )
    assert [o.noms for o in found] == ["A"]
    assert other_occupants(
        rows, structure="RG-PNR", fonction="COMREG", exclude_row_id="9"
    ) == []
    assert other_occupants(
        rows, structure="RG-BZV", fonction="CEMREG", exclude_row_id="9"
    ) == []


def test_chef_de_division_une_fois_par_division() -> None:
    rows = [
        _row(
            "1",
            "DPF",
            "CHEF DE DIVISION GESTION DU PERSONNEL",
            "A",
            "DIVISION GESTION DU PERSONNEL",
        ),
        _row(
            "2",
            "DPF",
            "CHEF DE DIVISION RECRUTEMENT ET FORMATION",
            "B",
            "DIVISION RECRUTEMENT ET FORMATION",
        ),
    ]
    assert other_occupants(
        rows,
        structure="DPF",
        fonction="CHEF DE DIVISION",
        subdivision="DIVISION RECRUTEMENT ET FORMATION",
        exclude_row_id="2",
    ) == []
    found = other_occupants(
        rows,
        structure="DPF",
        fonction="CHEF DE DIVISION GESTION DU PERSONNEL",
        subdivision="DIVISION GESTION DU PERSONNEL",
        exclude_row_id="9",
    )
    assert [o.noms for o in found] == ["A"]


def test_cds_est_le_chef_de_section() -> None:
    rows = [_row("1", "DPF", "CHEF DE SECTION FINANCES", "A", "SECTION FINANCES")]
    found = other_occupants(
        rows,
        structure="DPF",
        fonction="CHEF DE SECTION",
        subdivision="SECTION FINANCES",
        exclude_row_id="9",
    )
    assert [o.noms for o in found] == ["A"]
    assert other_occupants(
        rows,
        structure="DPF",
        fonction="CDS AUTRE SECTION",
        subdivision="SECTION FINANCES",
        exclude_row_id="9",
    ) == []


def test_la_fiche_elle_meme_ne_bloque_pas() -> None:
    rows = [_row("1", "COMGEND", "DIRECTEUR DSF", "GALIBAYI Roch")]
    assert other_occupants(
        rows, structure="COMGEND", fonction="DIRECTEUR DSF", exclude_row_id="1"
    ) == []


def test_controle_special_et_detaches_sans_poste_unique() -> None:
    rows = [
        _row("1", "CS-DPF", "ENQUETEUR", "A"),
        _row("2", "DETACHE", "STAGIAIRE", "B"),
    ]
    assert other_occupants(
        rows, structure="CS-DPF", fonction="ENQUETEUR", exclude_row_id="9"
    ) == []
    assert other_occupants(
        rows, structure="DETACHE", fonction="STAGIAIRE", exclude_row_id="9"
    ) == []
    assert occupants_by_fonction(rows, structure="LES DETACHES") == {}


def test_fonction_occupee_non_selectionnable() -> None:
    occupants = occupants_by_fonction(
        [_row("1", "DTRJD", "CHAUFFEUR", "NGOMA")],
        structure="DTRJD",
    )
    states = fonction_choice_states(
        ["CHAUFFEUR", "CDS D"],
        occupants,
        current="CDS D",
    )
    by_lib = {s.libelle: s for s in states}
    assert by_lib["CHAUFFEUR"].enabled is False
    assert "NGOMA" in by_lib["CHAUFFEUR"].label
    assert by_lib["CDS D"].enabled is True
    assert by_lib["CDS D"].label == "CDS D"


def test_comec_et_chefsec() -> None:
    rows = [
        _row("1", "EGN", "COMEC", "A"),
        _row("2", "EGN", "COMMANDANT DE L'ECOLE", "B", "DIVISION INSTRUCTION"),
        _row("3", "DPF", "CHEFSEC", "C", "SECRETARIAT"),
        _row("4", "DDT", "CHEF DE SECRETARIAT", "D", "SECRETARIAT"),
    ]
    found = other_occupants(rows, structure="EGN", fonction="CEMEC", exclude_row_id="9")
    assert found == []
    found = other_occupants(rows, structure="EGN", fonction="COMEC", exclude_row_id="9")
    assert {o.noms for o in found} == {"A", "B"}
    assert other_occupants(
        rows,
        structure="DDT",
        fonction="CHEFSEC",
        subdivision="SECRETARIAT",
        exclude_row_id="4",
    ) == []
    found = other_occupants(
        rows,
        structure="DPF",
        fonction="CHEF DE SECRETARIAT",
        subdivision="SECRETARIAT",
        exclude_row_id="9",
    )
    assert [o.noms for o in found] == ["C"]


def test_directeur_office_et_chef_de_pole() -> None:
    rows = [
        _row("1", "OCR", "DIRECTEUR OFFICE CENTRAL DU RENSEIGNEMENT", "A"),
        _row("2", "OCR", "CHEF DE POLE", "B", "POLE ANALYSE"),
        _row("3", "OCR", "CHEF DE POLE RENSEIGNEMENT", "C", "POLE RENSEIGNEMENT"),
    ]
    found = other_occupants(
        rows,
        structure="OCR",
        fonction="DIRECTEUR OCR",
        subdivision="POLE ANALYSE",
        exclude_row_id="9",
    )
    assert [o.noms for o in found] == ["A"]
    assert other_occupants(
        rows,
        structure="OCR",
        fonction="CHEF DE POLE",
        subdivision="POLE RENSEIGNEMENT",
        exclude_row_id="3",
    ) == []


def test_affectation_ancienne_ne_devient_pas_le_poste_actuel() -> None:
    assert affectation_devient_poste_actuel(
        new_date=date(2020, 1, 1),
        existing=[(date(2026, 8, 1), 4)],
    ) is False
    assert affectation_devient_poste_actuel(
        new_date=date(2026, 9, 1),
        existing=[(date(2026, 8, 1), 4)],
    ) is True
    assert affectation_devient_poste_actuel(new_date=date(2026, 9, 1), existing=[]) is True
