"""Tests repli FK subdivision organisationnelle (auto_create)."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.mysql_ref_etl import RefEtlCache, ensure_ref_subdiv_arme, resolve_carriere_fk_ids


class _FakeCur:
    _noeud_seq = 9000

    def execute(self, sql: str, params=None) -> None:
        if "INSERT INTO ref_subdivision_noeud" in sql:
            _FakeCur._noeud_seq += 1
            self._last_noeud_id = _FakeCur._noeud_seq
        if "SELECT id FROM ref_subdivision_noeud WHERE parent_id" in sql:
            pass
        if "SELECT id FROM ref_subdivision_arme" in sql:
            self._rsa_id = 7001

    def fetchone(self):
        if hasattr(self, "_rsa_id"):
            rid = self._rsa_id
            del self._rsa_id
            return (rid,)
        return (getattr(self, "_last_noeud_id", 9003),)


def test_ensure_ref_subdiv_arme_auto_create_org_leaf() -> None:
    cache = RefEtlCache()
    cache.structure_by_code["comgend"] = 42
    cur = _FakeCur()
    rid = ensure_ref_subdiv_arme(
        cur,
        cache,
        structure_id=42,
        subdivision_libelle="ETAT-MAJOR",
        flat_rows=[],
        auto_create=True,
    )
    assert rid == 7001
    leaf_id = cache.leaf_node_by_design_t["etat-major"]
    org_u = cache.misc_node_ids.get("org_u")
    assert org_u is not None
    assert leaf_id is not None
    assert leaf_id != org_u


def test_resolve_carriere_fk_passes_auto_create_to_subdiv() -> None:
    cache = RefEtlCache()
    cache.structure_by_code["comgend"] = 42
    cur = _FakeCur()
    row = ListeGeneraleRow(
        id="1",
        noms="X",
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
        subdivision_arme="COMMANDEMENT",
        diplome=None,
        date_obtention=None,
        structure="COMGEND",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    fk = resolve_carriere_fk_ids(cur, cache, row, flat_rows=[], auto_create=True)
    assert fk["structure_id"] == 42
    assert fk["subdivision_arme_id"] == 7001
