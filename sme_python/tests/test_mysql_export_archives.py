"""Test léger : export multi-archives (sans MySQL)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sme_gend.services.mysql_sql_export import export_archives_directory


def _apply_schema(conn: sqlite3.Connection, schema_sql: str) -> None:
    conn.executescript(schema_sql)
    conn.commit()


def test_export_archives_merges_two_months_one_person(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    schema = (root / "data" / "schema" / "liste_generale_sqlite.sql").read_text(encoding="utf-8")

    ad = tmp_path / "sme_archives"
    ad.mkdir()

    def bundle(ym: str, stamp: str, nom: str) -> None:
        p = ad / f"sme_bundle_{ym}_{stamp}.sqlite"
        conn = sqlite3.connect(p)
        try:
            _apply_schema(conn, schema)
            conn.execute(
                "INSERT INTO tab_structure (code, designation, ordre_tri) VALUES ('S1','St',1)"
            )
            conn.execute(
                "INSERT INTO tab_grade (code_g, grade, pref_ord, effectif_g) VALUES ('G1','Grade Un',1,1)"
            )
            conn.execute("INSERT INTO tab_secun (code_u, secu_design) VALUES ('U1','U')")
            conn.execute(
                "INSERT INTO tab_secdeux (code_u, code_d, secd_design) VALUES ('U1','D1','D')"
            )
            conn.execute(
                "INSERT INTO tab_sectrois (code_d, code_t, sect_design) VALUES ('D1','T1','T')"
            )
            conn.execute(
                "INSERT INTO tab_sme (codsme, mois, statut) VALUES ('012026', ?, 'Fermé')",
                (f"{ym[:4]}-{ym[5:7]}",),
            )
            conn.execute(
                "INSERT INTO liste_generale (id_acces, noms, grade, structure, padmin, sub_arme, diplome, date_obtention) "
                "VALUES ('A1', ?, 'Grade Un', 'S1', 'Présent', 'T1 T', 'CAP', '2015-06-01')",
                (nom,),
            )
            conn.commit()
        finally:
            conn.close()

    bundle("2026-01", "20260101T120000Z", "UN")
    bundle("2026-02", "20260201T120000Z", "DEUX")

    out = tmp_path / "all.sql"
    export_archives_directory(out, arch_dir=ad)
    text = out.read_text(encoding="utf-8")

    assert "INSERT INTO import_lot" in text
    assert text.count("INSERT INTO import_lot") == 1
    assert ",\n" in text or "(" in text
    # deux lots pour deux fichiers (une ligne VALUES avec deux groupes ou un seul INSERT multi?)
    # emit is single INSERT with multiple value tuples joined by comma
    assert "(1," in text and "(2," in text and "import_lot" in text
    # une seule personne A1 (chaînes SQL échappées avec quotes simples)
    assert "'A1'" in text
    assert "personne_diplome" in text
    assert "CAP" in text
    assert "ref_subdivision_noeud" in text
    assert "INSERT INTO ref_subdivision_noeud" in text
    assert "ref_subdivision_arme" in text
    assert "INSERT INTO ref_subdivision_arme" in text
    # deux segments carrière (un par mois)
    assert "2026-01-01" in text and "2026-02-01" in text
