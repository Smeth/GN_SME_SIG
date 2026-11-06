"""Requêtes MySQL ref_subdivision — compatibilité ONLY_FULL_GROUP_BY (erreur 3065)."""

from __future__ import annotations

import inspect


def test_fetch_subdiv_labels_sql_avoids_distinct_order_by_mismatch() -> None:
    """MySQL 3065 : DISTINCT + ORDER BY colonne hors SELECT est interdit."""
    from sme_gend.repositories.mysql_bundle_repositories import (
        MySQLStructureSubdivisionRepository,
    )

    src = inspect.getsource(MySQLStructureSubdivisionRepository.fetch_subdiv_labels_for_structure)
    assert "SELECT DISTINCT rsa.libelle" not in src
    assert "ORDER BY rsa.ordre_tri" in src
