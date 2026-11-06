"""Tests FK-first carrière (lecture ref prioritaire, écriture dénormalisée)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.mysql_ref_etl import RefEtlCache, prepare_carriere_write_fields, _cf

_REPO_SQL = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "sme_gend"
    / "repositories"
    / "mysql_agent_repository.py"
)


def _row(**kwargs) -> ListeGeneraleRow:
    base = dict(
        id="1",
        noms="TEST",
        date_naissance=None,
        lieu_naissance=None,
        grade="GradeLegacy",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="PresentLegacy",
        fonction="FoncLegacy",
        subdivision_arme="SubLegacy",
        diplome=None,
        date_obtention=None,
        structure="STRLEG",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    base.update(kwargs)
    return ListeGeneraleRow(**base)


class _FakeCur:
    def __init__(self, labels: dict[tuple[str, str, int], str]) -> None:
        self._labels = labels
        self.queries: list[tuple[str, tuple]] = []

    def execute(self, sql, params=()) -> None:
        self.queries.append((sql, params))

    def fetchone(self):
        if not self.queries:
            return None
        sql, params = self.queries[-1]
        if "FROM ref_grade" in sql:
            return (self._labels.get(("ref_grade", "libelle", int(params[0])), None),)
        if "FROM ref_structure" in sql:
            return (self._labels.get(("ref_structure", "code", int(params[0])), None),)
        if "FROM ref_position_admin" in sql:
            return (self._labels.get(("ref_position_admin", "libelle", int(params[0])), None),)
        if "FROM ref_fonction" in sql:
            return (self._labels.get(("ref_fonction", "libelle", int(params[0])), None),)
        if "FROM ref_subdivision_arme" in sql:
            return (self._labels.get(("ref_subdivision_arme", "libelle", int(params[0])), None),)
        return None


def test_select_liste_prefers_ref_when_fk_present() -> None:
    text = _REPO_SQL.read_text(encoding="utf-8")
    assert "IF(c.grade_id IS NOT NULL, rg.libelle, c.grade_libelle)" in text
    assert "IF(c.structure_id IS NOT NULL, rs.code, c.structure_code)" in text
    assert "IF(c.position_admin_id IS NOT NULL, rpa.libelle" in text
    assert "IF(c.fonction_id IS NOT NULL, rf.libelle" in text
    assert "IF(c.subdivision_arme_id IS NOT NULL, rsa.libelle" in text
    assert "LEFT JOIN ref_position_admin rpa" in text
    assert "LEFT JOIN ref_fonction rf" in text
    assert "prepare_carriere_write_fields" in text


def test_prepare_carriere_write_fields_uses_ref_labels(monkeypatch) -> None:
    cache = RefEtlCache()
    cache.structure_by_code[_cf("U001")] = 10
    cache.grade_by_libelle[_cf("GradeLegacy")] = 20
    cache.padmin_by_libelle[_cf("PresentLegacy")] = 30
    cache.fonction_by_libelle[_cf("FoncLegacy")] = 40
    cache.subdiv_arme_by_struct_leaf[(10, 99)] = 50
    cache.leaf_node_by_design_t[_cf("SubLegacy")] = 99

    monkeypatch.setattr(
        "sme_gend.services.mysql_ref_etl.ensure_ref_subdiv_arme",
        lambda *a, **k: 50,
    )

    cur = _FakeCur(
        {
            ("ref_grade", "libelle", 20): "Adjudant",
            ("ref_structure", "code", 10): "U001",
            ("ref_position_admin", "libelle", 30): "Présent",
            ("ref_fonction", "libelle", 40): "Chef",
            ("ref_subdivision_arme", "libelle", 50): "Infanterie",
        }
    )

    fields = prepare_carriere_write_fields(
        cur, cache, _row(structure="U001"), flat_rows=[], auto_create=False
    )

    assert fields["grade_id"] == 20
    assert fields["grade_libelle"] == "Adjudant"
    assert fields["structure_code"] == "U001"
    assert fields["position_admin_libelle"] == "Présent"
    assert fields["fonction_libelle"] == "Chef"
    assert fields["subdivision_arme_libelle"] == "Infanterie"


def test_prepare_carriere_write_fields_keeps_row_when_fk_missing() -> None:
    cache = RefEtlCache()
    cur = _FakeCur({})
    fields = prepare_carriere_write_fields(cur, cache, _row(), flat_rows=[], auto_create=False)
    assert fields["grade_id"] is None
    assert fields["grade_libelle"] == "GradeLegacy"
    assert fields["structure_code"] == "STRLEG"


def test_prepare_carriere_write_fields_uses_cached_labels_without_queries(monkeypatch) -> None:
    cache = RefEtlCache()
    cache.structure_by_code[_cf("U001")] = 10
    cache.structure_code_by_id[10] = "U001"
    cache.grade_by_libelle[_cf("GradeLegacy")] = 20
    cache.grade_libelle_by_id[20] = "Adjudant"
    cache.padmin_by_libelle[_cf("PresentLegacy")] = 30
    cache.padmin_libelle_by_id[30] = "Présent"
    cache.fonction_by_libelle[_cf("FoncLegacy")] = 40
    cache.fonction_libelle_by_id[40] = "Chef"
    cache.subdiv_arme_by_struct_leaf[(10, 99)] = 50
    cache.subdiv_arme_libelle_by_id[50] = "Infanterie"
    cache.leaf_node_by_design_t[_cf("SubLegacy")] = 99
    monkeypatch.setattr(
        "sme_gend.services.mysql_ref_etl.ensure_ref_subdiv_arme",
        lambda *a, **k: 50,
    )
    cur = _FakeCur({})

    fields = prepare_carriere_write_fields(
        cur, cache, _row(structure="U001"), flat_rows=[], auto_create=False
    )

    assert fields["grade_libelle"] == "Adjudant"
    assert fields["structure_code"] == "U001"
    assert fields["position_admin_libelle"] == "Présent"
    assert fields["fonction_libelle"] == "Chef"
    assert fields["subdivision_arme_libelle"] == "Infanterie"
    assert cur.queries == []
