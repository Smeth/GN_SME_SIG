from __future__ import annotations

from sme_gend.ui.bg_task import _ProgressReporter


def test_progress_detail_does_not_advance_step_count() -> None:
    steps: list[str] = []
    details: list[str] = []
    reporter = _ProgressReporter(steps.append, details.append)

    reporter("Liste générale — historique 2026-04…")
    reporter.detail("Liste générale — 250/1 000 agents traités")

    assert steps == ["Liste générale — historique 2026-04…"]
    assert details == ["Liste générale — 250/1 000 agents traités"]
