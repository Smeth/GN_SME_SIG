"""Tests hiérarchie subdivisions U→D→T."""

from __future__ import annotations

from sme_gend.models.subdiv_hierarchy import SubdivHierarchyNode


def test_subdiv_hierarchy_node_leaf_and_labels() -> None:
    u = SubdivHierarchyNode(
        node_id=2,
        parent_id=1,
        code="ESR",
        libelle="Escadron",
        pri_out=None,
        level=1,
    )
    t = SubdivHierarchyNode(
        node_id=5,
        parent_id=4,
        code="NGO",
        libelle="Pointe-Noire",
        pri_out=1.0,
        level=3,
        rsa_links=2,
    )
    assert not u.is_leaf
    assert t.is_leaf
    assert u.level_label == "Niveau U"
    assert t.level_label == "Niveau T"
