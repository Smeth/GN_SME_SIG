"""Tests rubriques strictes liste des présents."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.liste_present_rubric import (
    SANS_SUBDIVISION_KEY,
    build_strict_subdiv_blocks,
    iter_section_rubrics,
    should_emit_structure_code_rubric,
)


def _row(sub: str | None = None, nom: str = "DUPONT") -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms=nom,
        subdivision_arme=sub,
        ordre_tri=1,
        date_naissance=None,
        lieu_naissance=None,
        grade=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin=None,
        fonction=None,
        diplome=None,
        date_obtention=None,
        structure=None,
        obs=None,
        presseance=None,
    )


def test_build_strict_subdiv_blocks_respects_rsa_order_and_empty_rubrics():
    rows = [_row("CS-DPF", "A"), _row("DIV GP", "B")]
    blocks = build_strict_subdiv_blocks(
        rows,
        ["DIV GP", "CS-DPF", "AUTRE VIDE"],
        include_empty_rubrics=True,
    )
    labels = [lbl for lbl, _ in blocks]
    assert labels == ["DIV GP", "CS-DPF", "AUTRE VIDE"]
    assert len(blocks[2][1]) == 0


def test_build_strict_subdiv_blocks_sans_subdivision_first():
    rows = [_row(None, "X"), _row("CS-DPF", "Y")]
    blocks = build_strict_subdiv_blocks(rows, ["CS-DPF"], include_empty_rubrics=True)
    assert blocks[0][0] == SANS_SUBDIVISION_KEY
    assert len(blocks[0][1]) == 1
    assert blocks[1] == ("CS-DPF", rows[1:])


def test_should_emit_structure_code_only_when_sans_sub_only():
    blocks = [(SANS_SUBDIVISION_KEY, [_row()])]
    assert should_emit_structure_code_rubric(
        blocks, title_main="DIRECTION", title_sub="CS-DPF"
    )
    blocks2 = [
        (SANS_SUBDIVISION_KEY, [_row()]),
        ("DIV GP", [_row("DIV GP")]),
    ]
    assert not should_emit_structure_code_rubric(
        blocks2, title_main="DIRECTION", title_sub="CS-DPF"
    )


def test_iter_section_rubrics_structure_code_path():
    blocks = [
        (SANS_SUBDIVISION_KEY, [_row(nom="SOLO")]),
        ("DIV GP", []),
    ]
    seq = iter_section_rubrics("DIRECTION", "CS-DPF", blocks)
    kinds = [k for k, _ in seq]
    assert kinds == ["sec1", "sec2", "data", "sec2"]
    assert seq[1][1] == "CS-DPF"
    assert seq[3][1] == "DIV GP"


def test_iter_section_rubrics_subdiv_path():
    blocks = [
        (SANS_SUBDIVISION_KEY, [_row(nom="A")]),
        ("DIV GP", [_row("DIV GP", "B")]),
    ]
    seq = iter_section_rubrics("DIRECTION", "SECRETARIAT", blocks)
    kinds = [k for k, _ in seq]
    assert kinds == ["sec1", "sec2", "data", "sec2", "data"]
    assert seq[1][1] == "SECRETARIAT"
