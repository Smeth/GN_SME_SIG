"""Tests lien strict structure ↔ fonction."""

from __future__ import annotations

from pathlib import Path

import pytest

from sme_gend.config.settings import Settings
from sme_gend.services.referentiel_service import ReferentielService


@pytest.fixture(autouse=True)
def _no_mysql_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.repositories.mysql_common.ensure_mysql_schema",
        lambda _s: None,
    )


def _svc() -> ReferentielService:
    return ReferentielService(Settings(database_path=Path("test.mdb")))


def test_fonction_choices_for_structure_strict(monkeypatch) -> None:
    svc = _svc()

    class _Fonctions:
        def fetch_for_structure(self, code: str):
            assert code == "DPF"
            return ["CDS_BUREAUTIQUE", "COMGEND"]

    svc._fonctions = _Fonctions()
    assert svc.fonction_choices_for_structure("DPF") == [
        "CDS_BUREAUTIQUE",
        "COMGEND",
    ]
    assert svc.fonction_choices_for_structure("") == []


def test_fonction_choices_empty_structure_returns_empty(monkeypatch) -> None:
    svc = _svc()

    class _Fonctions:
        def fetch_for_structure(self, code: str):
            raise AssertionError("ne doit pas être appelé")

    svc._fonctions = _Fonctions()
    assert svc.fonction_choices_for_structure("   ") == []


def test_fonction_set_structure_links_delegates() -> None:
    svc = _svc()
    calls: list[tuple[int, list[str]]] = []

    class _Fonctions:
        def set_structure_links(self, fid: int, codes: list[str], *, expected=None) -> None:
            calls.append((fid, codes))

    svc._fonctions = _Fonctions()
    svc.fonction_set_structure_links(12, ["DPF", "CS-DPF"])
    assert calls == [(12, ["DPF", "CS-DPF"])]


def test_fonction_insert_rejects_duplicate_label() -> None:
    svc = _svc()

    class _Fonctions:
        def fetch_all(self):
            from sme_gend.models.fonction_ref import FonctionRefRow

            return [FonctionRefRow(id=1, libelle="COMGEND")]

        def insert(self, libelle: str) -> int:
            raise AssertionError("ne doit pas insérer un doublon")

    svc._fonctions = _Fonctions()
    with pytest.raises(ValueError, match="existe déjà"):
        svc.fonction_insert("comgend")


def test_fonction_insert_returns_id() -> None:
    svc = _svc()

    class _Fonctions:
        def fetch_all(self):
            return []

        def insert(self, libelle: str) -> int:
            assert libelle == "CDS"
            return 42

    svc._fonctions = _Fonctions()
    assert svc.fonction_insert("CDS") == 42


def test_fonction_count_usage_passes_id() -> None:
    svc = _svc()
    calls: list[tuple[str, int | None]] = []

    class _Fonctions:
        def count_usage(self, libelle: str, fonction_id: int | None = None) -> int:
            calls.append((libelle, fonction_id))
            return 3

    svc._fonctions = _Fonctions()
    assert svc.fonction_count_usage("COMGEND", 7) == 3
    assert calls == [("COMGEND", 7)]


def test_controle_special_et_detaches_sans_fonctions_liees() -> None:
    from sme_gend.services.fonction_structure_policy import structure_fonctions_non_liees

    assert structure_fonctions_non_liees("CS-DPF")
    assert structure_fonctions_non_liees("cs-dpf")
    assert structure_fonctions_non_liees("CONTRÔLE SPÉCIAL")
    assert structure_fonctions_non_liees("CONTROLE SPECIALE")
    assert structure_fonctions_non_liees("DETACHE")
    assert structure_fonctions_non_liees("LES DETACHES")
    assert not structure_fonctions_non_liees("DPF")
    assert not structure_fonctions_non_liees("RG-BZA")
    assert not structure_fonctions_non_liees("")
