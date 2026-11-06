"""Tests contrôles pré-clôture et permissions workflow."""

from __future__ import annotations

from sme_gend.services.workflow_permissions import WORKFLOW_ACTION_PERMISSION


def test_workflow_permissions_mapped():
    assert "workflow.close_month" in WORKFLOW_ACTION_PERMISSION.values()
    assert WORKFLOW_ACTION_PERMISSION["workflow.close_month"] == "workflow.close_month"


def test_pre_closure_report_blocking_property():
    from sme_gend.services.pre_closure_service import PreClosureCheck, PreClosureReport

    r = PreClosureReport(
        "2026-05",
        "2026-06",
        (
            PreClosureCheck("same_month", "block", "x", "y"),
            PreClosureCheck("ok", "ok", "ok", "ok"),
        ),
    )
    assert not r.can_proceed
    assert len(r.blocking) == 1
