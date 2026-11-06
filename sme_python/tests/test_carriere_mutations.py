"""Tests corrections scope / mutations carrière."""

from __future__ import annotations

from datetime import date

from sme_gend.config.liste_generale_scope import carriere_scope_sql
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService


def test_carriere_scope_sql_no_alias() -> None:
    sql, params = carriere_scope_sql("2026-04", alias="")
    assert "date_debut" in sql
    assert "sme_periode_id" in sql
    assert "c." not in sql
    # Ordre des paramètres : (ym, ref, ref, ym, ym, ref, ref)
    assert params[0] == "202604"
    assert params.count(date(2026, 4, 30)) == 2


def test_section_block_rsa_order_roundtrip() -> None:
    sec = SectionBlock(
        id="x",
        order=0,
        structure_code="A",
        group_by_subdivision=True,
        subdivision_order=["L1", "L2"],
        subdivision_rsa_order=[10, 20],
    )
    d = sec.to_dict()
    restored = SectionBlock.from_dict(d)
    assert restored.subdivision_rsa_order == [10, 20]


def test_layout_resolve_subdivision_order_merges_layout_and_db() -> None:
    """Ordre effectif = layout configuré fusionné avec les libellés base (RSA en repli)."""
    svc = LayoutConfigService.__new__(LayoutConfigService)
    sec = SectionBlock(
        id="x",
        order=0,
        structure_code="A",
        group_by_subdivision=True,
        subdivision_order=["legacy"],
        subdivision_rsa_order=[1],
    )

    def fake_rsa(_ids):
        return ["from_rsa"]

    def fake_db(_code):
        return []

    svc._rsa_labels = fake_rsa  # type: ignore[method-assign]
    svc.subarme_labels_for_structure = fake_db  # type: ignore[method-assign]
    # Ordre layout conservé, libellés base absents du layout ajoutés en fin.
    assert svc.resolve_subdivision_order_labels(sec) == ["legacy", "from_rsa"]
