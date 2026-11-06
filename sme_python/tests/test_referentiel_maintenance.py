"""Tests maintenance référentiels (migration org, layout)."""

from __future__ import annotations

from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService
from sme_gend.services.referentiel_maintenance import (
    _repoint_rsa_noeud_feuille,
    migrate_misplaced_org_nodes,
)
from sme_gend.services.mysql_ref_etl import RefEtlCache


class _MigrateCur:
    def __init__(self) -> None:
        self.deleted: list[int] = []
        self.updated_rsa: list[tuple[int, int]] = []
        self._children: dict[int, list[int]] = {1: [99]}
        self._nodes = {
            99: (1, "OIABC", "ETAT-MAJOR"),
        }

    def execute(self, sql: str, params=None) -> None:
        s = " ".join(sql.split())
        if "SELECT COUNT(*) FROM ref_subdivision_noeud WHERE parent_id" in s:
            pid = int(params[0])
            self._last_count = len(self._children.get(pid, []))
        if "SELECT parent_id FROM ref_subdivision_noeud WHERE id" in s:
            nid = int(params[0])
            self._parent_row = (self._nodes.get(nid, (None, "", ""))[0],)
        if "SELECT n.id, n.code, n.libelle" in s and "parent_id = %s" in s:
            self._migrate_rows = [(99, "OIABC", "ETAT-MAJOR")]
        if "SELECT id, structure_id FROM ref_subdivision_arme WHERE noeud_feuille_id" in s:
            src = getattr(self, "_rsa_source", None)
            self._rsa_rows = list(src) if src is not None else []
            return
        if "SELECT id FROM ref_subdivision_arme" in s and "structure_id = %s" in s and "noeud_feuille_id = %s" in s:
            if hasattr(self, "_dup_preset"):
                self._dup_row = self._dup_preset
            else:
                self._dup_row = None
            return
        if "UPDATE ref_subdivision_arme SET noeud_feuille_id" in s:
            self.updated_rsa.append((int(params[1]), int(params[0])))
        if "UPDATE carriere_situation SET subdivision_arme_id" in s:
            self.merged_carriere = (int(params[0]), int(params[1]))
        if "DELETE FROM ref_subdivision_arme WHERE id" in s:
            self.deleted_rsa = int(params[0])
        if "DELETE FROM ref_subdivision_noeud WHERE id" in s:
            self.deleted.append(int(params[0]))
        if "INSERT INTO ref_subdivision_noeud" in s:
            pass
        if "SELECT id FROM ref_subdivision_noeud WHERE parent_id" in s:
            self._last_noeud_id = 2001

    def fetchall(self):
        if hasattr(self, "_migrate_rows"):
            rows = self._migrate_rows
            del self._migrate_rows
            return rows
        if hasattr(self, "_rsa_rows"):
            rows = self._rsa_rows
            del self._rsa_rows
            return rows
        return []

    def fetchone(self):
        if hasattr(self, "_dup_row"):
            row = self._dup_row
            del self._dup_row
            return row
        if hasattr(self, "_parent_row"):
            row = self._parent_row
            del self._parent_row
            return row
        if hasattr(self, "_last_count"):
            c = self._last_count
            del self._last_count
            return (c,)
        return (2001,)


def test_migrate_misplaced_org_nodes_deletes_old_leaf(monkeypatch) -> None:
    cache = RefEtlCache()
    cur = _MigrateCur()
    cur._rsa_source = [(1, 2)]

    def fake_org_u(c, cache):
        cache.misc_node_ids["org_u"] = 100
        return 100

    def fake_upsert(c, *, parent_id, code, libelle, pri_out):
        if parent_id == 100:
            return 101
        return 102

    monkeypatch.setattr(
        "sme_gend.services.referentiel_maintenance._ensure_org_subdiv_u", fake_org_u
    )
    monkeypatch.setattr(
        "sme_gend.services.referentiel_maintenance._upsert_subdiv_node", fake_upsert
    )
    monkeypatch.setattr(
        "sme_gend.services.referentiel_maintenance._node_depth", lambda c, n: 1
    )

    n = migrate_misplaced_org_nodes(cur, cache)
    assert n == 1
    assert 99 in cur.deleted
    assert cur.updated_rsa == [(1, 102)]


def test_repoint_rsa_merges_duplicate_struct_feuille() -> None:
    cur = _MigrateCur()
    cur._rsa_source = [(10, 5)]
    cur._dup_preset = (20,)

    _repoint_rsa_noeud_feuille(cur, 99, 102)

    assert cur.merged_carriere == (20, 10)
    assert cur.deleted_rsa == 10
    assert not hasattr(cur, "updated_rsa") or cur.updated_rsa == []


def test_labels_equivalent_fuzzy() -> None:
    from sme_gend.services.referentiel_maintenance import _labels_equivalent

    assert _labels_equivalent(
        "Division Action Social Chancellerie",
        "DIVISION ACTION SOCIALE ET CHANCELLERIE",
    )
    assert _labels_equivalent("Secrétariat DPF", "SECRETARIAT")
    assert not _labels_equivalent("DASC", "DIVISION ACTION SOCIALE ET CHANCELLERIE")


def test_normalize_subdiv_libelles_merges_variants() -> None:
    from sme_gend.services.referentiel_maintenance import normalize_subdiv_libelles_in_carriere

    class _Cur:
        def __init__(self) -> None:
            self.updates: list[tuple] = []

        def execute(self, sql: str, params=None) -> None:
            s = " ".join(sql.split())
            if "GROUP BY c.structure_id" in s:
                self._rows = [
                    (10, "SECRETARIAT", 20),
                    (10, "Secrétariat DPF", 3),
                ]
            elif "UPDATE carriere_situation" in s:
                self.updates.append(tuple(params or ()))
                self.rowcount = 3

        def fetchall(self):
            return getattr(self, "_rows", [])

    cur = _Cur()
    n, warns = normalize_subdiv_libelles_in_carriere(cur)
    assert n == 3
    assert cur.updates and cur.updates[0][0] in ("SECRETARIAT", "Secrétariat DPF")


def test_dedupe_rsa_merges_and_deletes_unused() -> None:
    from sme_gend.services.referentiel_maintenance import dedupe_rsa_from_carriere

    class _Cur:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple | None]] = []
            self._step = 0

        def execute(self, sql: str, params=None) -> None:
            self.executed.append((" ".join(sql.split()), params))
            s = " ".join(sql.split())
            if "GROUP BY c.structure_id" in s:
                self._canon = [(10, "SECRETARIAT", 5), (10, "SECTION FINANCES", 1)]
            elif "FROM ref_subdivision_arme rsa" in s and "WHERE rsa.actif" in s:
                self._rsa = [
                    (1, 10, "Secrétariat DPF", "DPF"),
                    (2, 10, "SECRETARIAT", "DPF"),
                    (3, 10, "SECTION FICHIER", "DPF"),
                ]
            elif "subdivision_arme_id = %s" in s and "COUNT" in s:
                rid = int(params[0]) if params else 0
                self._usage = {1: 0, 2: 5, 3: 0}.get(rid, 0)

        def fetchall(self):
            s = self.executed[-1][0]
            if "GROUP BY c.structure_id" in s:
                return getattr(self, "_canon", [])
            if "FROM ref_subdivision_arme rsa" in s and "WHERE rsa.actif" in s:
                return getattr(self, "_rsa", [])
            return []

        def fetchone(self):
            return (getattr(self, "_usage", 0),)

    cur = _Cur()
    merged, deleted, deactivated, _warn = dedupe_rsa_from_carriere(cur, mois="2026-05")
    assert merged == 1
    assert deleted == 1
    assert deactivated == 0


def test_remove_subdivision_keeps_other_rsa_order(monkeypatch) -> None:
    svc = LayoutConfigService.__new__(LayoutConfigService)
    monkeypatch.setattr(
        svc,
        "_rsa_labels",
        lambda rsa_ids: ["X", "Y"] if len(rsa_ids) == 2 else [],
    )
    cfg = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="a",
                order=0,
                structure_code="A",
                group_by_subdivision=True,
                subdivision_order=["X", "Y"],
                subdivision_rsa_order=[101, 102],
            )
        ]
    )
    out = svc.remove_subdivision_from_layout(cfg, "A", "X")
    assert out.sections[0].subdivision_rsa_order == [102]
