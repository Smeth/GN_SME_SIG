"""Historique dossier personnel (audit JSONL)."""

from __future__ import annotations

from sme_gend.services import personnel_dossier_history as h


def test_build_audit_timeline_splits_career_and_profile(monkeypatch, tmp_path) -> None:
    p = tmp_path / "personnel_audit.jsonl"
    p.write_text(
        '{"action":"update","personnel_id":"42","ts_utc":"2026-01-15T10:00:00+00:00",'
        '"sme_mois_context":"2026-01","changes":{'
        '"grade":{"old":"LT","new":"CNE"},'
        '"noms":{"old":"DUPONT A","new":"DUPONT Albert"}'
        "}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(h, "personnel_audit_file_path", lambda: p)
    entries = h.build_audit_timeline("42")
    assert len(entries) == 1
    assert "grade" in entries[0].career_changes
    assert "noms" in entries[0].profile_changes


def test_field_label_fr() -> None:
    assert h.field_label("structure") == "Structure"
    assert h.field_label("unknown_x") == "Unknown x"
