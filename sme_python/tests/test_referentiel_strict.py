"""Tests référentiels stricts (ref_* sans repli DISTINCT carrière)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sme_gend.config.settings import Settings
from sme_gend.models.fonction_ref import FonctionRefRow
from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.services.referentiel_service import ReferentielService


@pytest.fixture(autouse=True)
def _no_mysql_schema(monkeypatch) -> None:
    monkeypatch.setattr(
        "sme_gend.repositories.mysql_common.ensure_mysql_schema",
        lambda _s: None,
    )


def _svc() -> ReferentielService:
    return ReferentielService(Settings(database_path=Path("test.mdb")))


def test_padmin_choices_from_ref_only_when_mysql_available(monkeypatch) -> None:
    svc = _svc()

    class _Padmin:
        def fetch_all(self):
            return [PadminRefRow(libelle="Présent", compte_comme_present=True, ordre_tri=1)]

    svc._padmin = _Padmin()

    def _fail_distinct():
        raise AssertionError("fetch_distinct_padmin ne doit pas être appelé")

    monkeypatch.setattr(svc._liste_generale, "fetch_distinct_padmin", _fail_distinct)

    assert svc.position_admin_choices() == ["Présent"]


def test_padmin_empty_ref_returns_empty_not_liste_distinct(monkeypatch) -> None:
    svc = _svc()

    class _Padmin:
        def fetch_all(self):
            return []

    svc._padmin = _Padmin()

    def _fail_distinct():
        raise AssertionError("fetch_distinct_padmin ne doit pas être appelé")

    monkeypatch.setattr(svc._liste_generale, "fetch_distinct_padmin", _fail_distinct)

    assert svc.position_admin_choices() == []
    assert svc.padmins() == []


def test_subdiv_combo_uses_ref_not_liste(monkeypatch) -> None:
    svc = _svc()

    class _Flat:
        design_t = "Infanterie"

    monkeypatch.setattr(
        svc._subdivisions,
        "fetch_flat_hierarchy",
        lambda: [_Flat()],
    )
    monkeypatch.setattr(
        svc._struct_sub,
        "fetch_distinct_subdiv_arme_labels",
        lambda: ["Cavalerie"],
    )

    def _fail_liste():
        raise AssertionError("fetch_distinct_subarme ne doit pas être appelé")

    monkeypatch.setattr(svc._liste_generale, "fetch_distinct_subarme", _fail_liste)

    choices = svc.subdivision_arme_combo_choices()
    assert choices == ["Cavalerie", "Infanterie"]


def test_subdivisions_for_structure_from_ref(monkeypatch) -> None:
    svc = _svc()
    monkeypatch.setattr(
        svc._struct_sub,
        "fetch_subdiv_labels_for_structure",
        lambda code: ["Sub-A"] if code == "U001" else [],
    )

    def _fail_liste(_code: str):
        raise AssertionError("liste_generale ne doit pas être interrogée")

    monkeypatch.setattr(
        svc._liste_generale,
        "fetch_distinct_subarme_for_structure",
        _fail_liste,
    )

    assert svc.subdivisions_arme_for_structure("U001") == ["Sub-A"]


def test_subdiv_choices_for_structure_not_global_hierarchy(monkeypatch) -> None:
    svc = _svc()
    monkeypatch.setattr(
        svc._struct_sub,
        "fetch_subdiv_labels_for_structure",
        lambda code: ["Sub-RSA"] if code == "COMGEND" else [],
    )
    monkeypatch.setattr(
        svc._liste_generale,
        "fetch_distinct_subarme_for_structure",
        lambda code: ["Sub-Data"] if code == "COMGEND" else [],
    )

    def _fail_flat():
        raise AssertionError("subdivisions_flat ne doit pas alimenter le filtre par structure")

    monkeypatch.setattr(svc, "subdivisions_flat", _fail_flat)

    assert svc.subdivision_arme_choices_for_structure("COMGEND") == ["Sub-Data", "Sub-RSA"]
    assert svc.subdivision_arme_choices_for_structure("AUTRE") == []


def test_fonctions_fetch_distinct_ref_only(monkeypatch) -> None:
    from sme_gend.repositories.mysql_bundle_repositories import MySQLFonctionsRepository

    repo = MySQLFonctionsRepository(Settings(database_path=Path("x")))
    monkeypatch.setattr(
        repo,
        "fetch_all",
        lambda: [FonctionRefRow(id=1, libelle="Chef de section")],
    )
    assert repo.fetch_distinct() == ["Chef de section"]


def test_fonctions_fetch_usage_counts_split_query(monkeypatch) -> None:
    from sme_gend.repositories.mysql_bundle_repositories import MySQLFonctionsRepository

    calls: list[str] = []

    class _Cur:
        def execute(self, sql, _params=None):
            calls.append(" ".join(sql.split()))

        def fetchall(self):
            if calls[-1].startswith("SELECT id, libelle"):
                return [(1, "COMGEND"), (2, "ORPHELIN")]
            if "fonction_id IS NOT NULL" in calls[-1]:
                return [(1, 3)]
            if "fonction_id IS NULL" in calls[-1]:
                return [("ORPHELIN", 2)]
            return []

        def fetchone(self):
            return (0,)

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    repo = MySQLFonctionsRepository(Settings(database_path=Path("x")))
    monkeypatch.setattr(repo, "_connect", lambda **kwargs: _Conn())

    out = repo.fetch_usage_counts()

    assert out == {1: 3, 2: 2}
    joined = " ".join(calls).upper()
    assert " LEFT JOIN " not in joined
    assert "est_actuelle = 1" in joined.lower()
