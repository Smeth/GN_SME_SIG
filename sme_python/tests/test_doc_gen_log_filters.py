"""Filtres vue Historique documents (type, mois, recherche libre)."""

from __future__ import annotations

from sme_gend.services.doc_gen_log import doc_entry_passes_filters


def test_doc_entry_search_by_person_name() -> None:
    entry = {
        "doc_type": "liste_present",
        "mois": "2026-04",
        "persons": ["DUPONT Jean", "MARTIN Paul"],
        "dest": r"C:\out\present.xlsx",
    }
    assert doc_entry_passes_filters(entry, search_query="dupont")
    assert not doc_entry_passes_filters(entry, search_query="bernard")


def test_doc_entry_search_by_dest_and_type_label() -> None:
    entry = {
        "doc_type": "numerique",
        "format": "xlsx",
        "dest": r"D:\exports\numerique_2026-05.xlsx",
        "mois": "2026-05",
    }
    labels = {"numerique": "Situation numérique"}
    assert doc_entry_passes_filters(
        entry, search_query="situation", type_labels=labels
    )
    assert doc_entry_passes_filters(entry, type_key="numerique", mois_filter="2026-05")
    assert doc_entry_passes_filters(entry, mois_filter="2026")
    assert not doc_entry_passes_filters(entry, mois_filter="2026-07")
    assert not doc_entry_passes_filters(entry, type_key="detaches")
