"""Import / export CSV de l'historique des documents — jointure sans écrasement."""

from __future__ import annotations

from sme_gend.services.doc_gen_log import (
    doc_entry_fingerprint,
    export_doc_log_csv,
    parse_doc_log_csv,
    plan_doc_log_merge,
)


def _sample() -> dict:
    return {
        "ts_utc": "2026-04-12T08:15:01+00:00",
        "doc_type": "attestation",
        "format": "PDF",
        "mois": "2026-04",
        "count": 1,
        "persons": ["DUPONT Jean"],
        "dest": r"C:\out\attestation.pdf",
        "actor": "admin",
        "meta": {"modele": "limite_age"},
    }


def test_csv_roundtrip_keeps_fingerprint() -> None:
    original = _sample()
    parsed, invalid = parse_doc_log_csv(export_doc_log_csv([original]))
    assert invalid == 0
    assert len(parsed) == 1
    assert doc_entry_fingerprint(parsed[0]) == doc_entry_fingerprint(original)


def test_merge_appends_without_dropping_or_duplicating() -> None:
    current = [_sample()]
    extra = {
        **_sample(),
        "ts_utc": "2026-05-02T09:00:00+00:00",
        "persons": ["MARTIN Paul"],
        "dest": r"C:\out\autre.pdf",
    }
    csv_text = export_doc_log_csv([current[0], extra])
    incoming, invalid = parse_doc_log_csv(csv_text)
    assert invalid == 0
    to_add, skipped = plan_doc_log_merge(current, incoming)
    assert skipped == 1
    assert len(to_add) == 1
    assert to_add[0]["persons"] == ["MARTIN Paul"]
    assert len(current) == 1
    assert current[0]["persons"] == ["DUPONT Jean"]


def test_parse_ignores_row_without_date_and_accepts_comma() -> None:
    text = (
        "ts_utc,doc_type,format,mois,count,persons,dest,actor,meta\n"
        ",attestation,pdf,2026-04,1,DUPONT,,,,\n"
        "12/05/2026 14:30:00,soit_transmis,docx,2026-05,1,MARTIN,C:\\s.docx,chef,\n"
    )
    entries, invalid = parse_doc_log_csv(text)
    assert invalid == 1
    assert len(entries) == 1
    assert entries[0]["ts_utc"] == "2026-05-12T14:30:00"
    assert entries[0]["doc_type"] == "soit_transmis"
