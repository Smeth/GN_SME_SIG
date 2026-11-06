"""Tests organigramme / mise en page liste des présents."""

from __future__ import annotations

from sme_gend.config.liste_generale_scope import carriere_scope_sql
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.layout_config_service import LayoutConfigService


def test_carriere_scope_sql_uses_view_mois() -> None:
    sql, params = carriere_scope_sql("2026-04")
    assert "date_debut" in sql
    assert params[0] == "202604"


def test_carriere_scope_sql_fallback_est_actuelle() -> None:
    sql, params = carriere_scope_sql(None)
    assert "est_actuelle" in sql
    assert params == ()


def test_remove_structure_from_layout() -> None:
    svc = LayoutConfigService.__new__(LayoutConfigService)
    cfg = ListeLayoutConfig(
        sections=[
            SectionBlock(id="a", order=0, structure_code="A", group_by_subdivision=True),
            SectionBlock(id="b", order=1, structure_code="B", group_by_subdivision=True),
        ]
    )
    out = svc.remove_structure_from_layout(cfg, "A")
    assert [s.structure_code for s in out.sections] == ["B"]
    assert out.sections[0].order == 0


def test_remove_subdivision_from_layout() -> None:
    svc = LayoutConfigService.__new__(LayoutConfigService)
    cfg = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="a",
                order=0,
                structure_code="A",
                group_by_subdivision=True,
                subdivision_order=["X", "Y"],
            )
        ]
    )
    out = svc.remove_subdivision_from_layout(cfg, "A", "X")
    assert out.sections[0].subdivision_order == ["Y"]


def test_remove_subdivision_keeps_other_rsa_order(monkeypatch) -> None:
    svc = LayoutConfigService.__new__(LayoutConfigService)
    monkeypatch.setattr(
        svc,
        "_rsa_labels",
        lambda rsa_ids: ["X", "Y"] if len(rsa_ids) == 2 else [],
    )
    cfg = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="a",
                order=0,
                structure_code="A",
                group_by_subdivision=True,
                subdivision_order=["X", "Y"],
                subdivision_rsa_order=[101, 102],
            )
        ]
    )
    out = svc.remove_subdivision_from_layout(cfg, "A", "X")
    assert out.sections[0].subdivision_rsa_order == [102]
