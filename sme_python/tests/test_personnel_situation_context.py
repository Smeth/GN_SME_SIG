from __future__ import annotations

from dataclasses import replace

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import personnel_situation_context as psc
from sme_gend.services.personnel_situation_context import (
    build_situation_en_cours,
    section_title,
)


def _row(**kwargs) -> ListeGeneraleRow:
    base = ListeGeneraleRow(
        id="1",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        grade="MDL",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    return replace(base, **kwargs) if kwargs else base


def test_section_title() -> None:
    assert section_title() == "SITUATION EN COURS"


def test_present_no_situation_block() -> None:
    assert build_situation_en_cours(_row(), object()) is None


def test_detache_situation_sigle(monkeypatch) -> None:
    from sme_gend.models.detaches_file_config import DetachesFileConfig

    monkeypatch.setattr(
        psc,
        "_build_sec_lookup",
        lambda _s: {"ESC 1": "Escadron 1"},
    )
    monkeypatch.setattr(
        "sme_gend.services.detaches_config_service.DetachesConfigService.load",
        lambda self: DetachesFileConfig(),
    )
    monkeypatch.setattr(
        "sme_gend.services.referentiel_service.ReferentielService.structures",
        lambda self: [],
    )

    row = _row(
        position_admin="Détaché",
        structure="DETACHE",
        subdivision_arme="ESC 1",
    )
    sit = build_situation_en_cours(row, object())
    assert sit is not None
    assert any(v == "Détaché" for _, v in sit.fields)
    assert any(k == "Sigle (subdivision)" and v == "ESC 1" for k, v in sit.fields)


def test_mission_structure(monkeypatch) -> None:
    monkeypatch.setattr(psc, "_safe_sat_repo", lambda _s: None)
    monkeypatch.setattr(psc, "_safe_events", lambda _s: None)

    row = _row(position_admin="Mission", access_join_id="LEG-1")
    sit = build_situation_en_cours(row, object())
    assert sit is not None
    assert sit.fields[0] == ("Situation", "Mission")
    assert sit.missing_satellite_detail is True
