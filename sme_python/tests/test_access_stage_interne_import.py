"""Import Access TabStageInterne → parcours_stage INTERNE."""

from __future__ import annotations

from unittest.mock import MagicMock

from sme_gend.services.mysql_access_numerique_import import import_stages_from_access


def test_import_stages_from_access_reads_tab_stage_interne() -> None:
    queries: list[str] = []

    def pull(sql: str):
        queries.append(sql)
        return ([], None)

    cur = MagicMock()
    counts = import_stages_from_access(
        cur,
        pull,
        mois="2026-05",
        sme_periode_id=99,
        import_lot_id=None,
    )

    assert any("TabStageInterne" in q for q in queries)
    assert "parcours_stage_interne" in counts
    assert counts["parcours_stage_interne"] == 0
