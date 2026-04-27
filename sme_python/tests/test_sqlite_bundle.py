"""Schéma SQLite et clés de jointure liste / satellites (sans base Access)."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_sqlite import PersonnelSqliteRow
from sme_gend.repositories.sqlite_common import apply_sqlite_schema


def test_apply_sqlite_schema_creates_core_tables(tmp_path: Path) -> None:
    db = tmp_path / "x.sqlite"
    apply_sqlite_schema(db)
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = {r[0] for r in cur.fetchall()}
    assert "liste_generale" in names
    assert "tab_structure" in names
    assert "tab_grade" in names
    assert "tab_mission" in names
    con.close()


def test_satellite_join_key_prefers_access_id() -> None:
    row = ListeGeneraleRow(
        id="99",
        noms=None,
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
        access_join_id="ACC-1",
    )
    assert row.satellite_join_key() == "ACC-1"


def test_satellite_join_key_falls_back_to_id() -> None:
    row = ListeGeneraleRow(
        id="12",
        noms=None,
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    assert row.satellite_join_key() == "12"


def test_personnel_sqlite_sets_access_join_id() -> None:
    acc = ListeGeneraleRow(
        id="EXT-42",
        noms="Dupont",
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    ps = replace(PersonnelSqliteRow.from_liste_generale_access(acc), id=1)
    lr = ps.to_liste_generale_row()
    assert lr.id == "1"
    assert lr.access_join_id == "EXT-42"
    assert lr.satellite_join_key() == "EXT-42"
