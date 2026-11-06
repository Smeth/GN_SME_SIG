"""Tests harmonisation manuelle (clustering libellés, pas de fuzzy auto en UI)."""

from __future__ import annotations

from sme_gend.services.referentiel_maintenance import (
    _cluster_canonical_labels,
    _pick_canonical_from_cluster,
)


def test_pick_canonical_prefers_highest_count() -> None:
    cluster = [("B", 2), ("AAAA", 10), ("C", 1)]
    assert _pick_canonical_from_cluster(cluster) == "AAAA"


def test_cluster_canonical_labels_merges_equivalent() -> None:
    rows = [("SECRETARIAT", 5), ("Secrétariat DPF", 2)]
    out = _cluster_canonical_labels(rows)
    assert len(out) == 1
    assert out[0][1] == 7
