"""Tests outils subdivisions layout."""

from sme_gend.services.liste_present_subdiv_tools import (
    dedupe_subdiv_labels,
    merge_subdiv_labels,
    sort_subdiv_labels_alpha,
    sort_subdiv_labels_by_count,
)


def test_merge_subdiv_labels():
    labels = ["CABINET", "CABINET COMGEND", "CONSEILLER"]
    out = merge_subdiv_labels(labels, ["CABINET", "CABINET COMGEND"], "CABINET COMGEND")
    assert out == ["CABINET COMGEND", "CONSEILLER"]


def test_dedupe_case_insensitive():
    assert dedupe_subdiv_labels(["A", "a", "B"]) == ["A", "B"]


def test_sort_alpha_sans_last():
    out = sort_subdiv_labels_alpha(["Z", "(Sans subdivision)", "A"])
    assert out == ["A", "Z", "(Sans subdivision)"]


def test_sort_by_effectif():
    labels = ["B", "A", "C"]
    counts = {"A": 10, "B": 5, "C": 20}
    assert sort_subdiv_labels_by_count(labels, counts) == ["C", "A", "B"]
