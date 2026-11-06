"""Tests Phase C — résumé export numérique et journal doc_gen."""

from __future__ import annotations

from sme_gend.services.numerique_export_summary import (
    NumeriqueExportSummary,
    format_numerique_export_preview_text,
)


def test_numerique_export_summary_satellites_total() -> None:
    s = NumeriqueExportSummary(
        export_mois="2026-05",
        mois_actif="2026-05",
        is_archive_view=False,
        agent_count=120,
        missions=3,
        malades=2,
        deserteurs=0,
        stages_loc=1,
        stages_ex=4,
    )
    assert s.satellites_total == 10


def test_format_numerique_export_preview_text_archive() -> None:
    s = NumeriqueExportSummary(
        export_mois="2025-03",
        mois_actif="2026-05",
        is_archive_view=True,
        agent_count=50,
        missions=1,
        malades=0,
        deserteurs=0,
        stages_loc=0,
        stages_ex=0,
    )
    txt = format_numerique_export_preview_text(s)
    assert "2025-03" in txt
    assert "50" in txt
    assert "2026-05" in txt
    assert "archive" in txt.lower() or "session" in txt.lower()


def test_log_doc_generation_payload_mois_meta(monkeypatch) -> None:
    from sme_gend.services import doc_gen_log

    captured: list[dict] = []

    def _fake_append(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(doc_gen_log, "_append_doc_gen_mysql", _fake_append)
    doc_gen_log.log_doc_generation(
        doc_type="numerique",
        format="xlsx",
        count=42,
        dest_path="/tmp/test.xlsx",
        mois="2026-05",
        meta={"missions": 3},
    )
    assert captured
    assert captured[0]["mois"] == "2026-05"
    assert captured[0]["meta"] == {"missions": 3}
