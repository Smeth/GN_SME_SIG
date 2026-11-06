"""Tests moteur liste des présents (ALGORITHME_LISTE_PRESENTS.md)."""

from __future__ import annotations

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.structure import StructureRow
from sme_gend.models.liste_layout import ListeLayoutConfig, SectionBlock
from sme_gend.services.liste_present_populate import (
    NO_SUBDIVISION_STRUCTURES,
    find_people,
    group_personnel,
    person_key,
    populate_liste_sections,
)
from sme_gend.services.liste_present_rg_template import (
    is_rg_compagnie,
    template_sub_matches_mdb,
)
from sme_gend.services.liste_present_rubric import (
    MERGED_SUBDIVISIONS_KEY,
    RG_COMMANDEMENT_MARKER,
    iter_section_rubrics,
)


def _st(*codes: str) -> dict[str, StructureRow]:
    return {
        c: StructureRow(code=c, designation=c, ordre_tri=1)
        for c in codes
    }


def _row(
    *,
    id: str = "1",
    structure: str = "RG-BZV",
    sub: str = "COMMANDEMENT",
    nom: str = "DUPONT",
    grade: str = "MDC",
    ordre: int = 1,
    fonction: str | None = None,
) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=id,
        noms=nom,
        structure=structure,
        subdivision_arme=sub,
        grade=grade,
        ordre_tri=ordre,
        date_naissance=None,
        lieu_naissance=None,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=fonction,
        diplome=None,
        date_obtention=None,
        obs=None,
        presseance=None,
    )


def test_template_sub_matches_rg_commandement_alias():
    assert template_sub_matches_mdb("RG-BZV", "COMMANDEMENT RG-BZV", "COMMANDEMENT")
    assert not template_sub_matches_mdb("RG-BZV", "COMMANDEMENT RG-BZV", "ETAT-MAJOR")


def test_template_sub_matches_onu_alias():
    assert template_sub_matches_mdb("RG-BZV", "ONU", "DETACHEMENT ONU")


def test_is_rg_compagnie_excludes_brigade_recherche():
    assert is_rg_compagnie("COMPAGNIE DE GENDARMERIE TERRITORIALE BRAZZAVILLE 1")
    assert not is_rg_compagnie(
        "BRIGADE DE RECHERCHE COMPAGNIE DE GENDARMERIE TERRITORIALE BRAZZAVILLE 1"
    )


def test_person_key_prefers_id():
    r = _row(id="42", nom="X")
    assert person_key(r) == "id:42"


def test_find_people_rg_fuzzy():
    rows = [_row(sub="COMMANDEMENT", id="1"), _row(sub="ETAT-MAJOR", id="2", nom="MARTIN")]
    groups = group_personnel(rows)
    cmd = find_people(groups, "RG-BZV", "COMMANDEMENT RG-BZV")
    assert len(cmd) == 1
    assert cmd[0].id == "1"


def test_find_people_exact_non_rg():
    rows = [_row(structure="COMGEND", sub="Direction", id="1")]
    groups = group_personnel(rows)
    found = find_people(groups, "COMGEND", "Direction")
    assert len(found) == 1


def test_populate_cs_dpf_merges_and_sorts_by_grade():
    assert "CS-DPF" in NO_SUBDIVISION_STRUCTURES
    rows = [
        _row(structure="CS-DPF", sub="DIV A", id="1", grade="MDC", ordre=2),
        _row(structure="CS-DPF", sub="DIV B", id="2", grade="COL", ordre=1, nom="COLONEL"),
    ]
    layout = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="cs",
                order=0,
                structure_code="CS-DPF",
                subdivision_order=["DIV A", "DIV B"],
            )
        ]
    )
    sections, missing, assigned = populate_liste_sections(
        layout, rows, _st("CS-DPF"), include_orphans=False
    )
    assert not missing
    assert len(assigned) == 2
    assert len(sections) == 1
    blocks = sections[0][3]
    assert blocks[0][0] == MERGED_SUBDIVISIONS_KEY
    merged = blocks[0][1]
    assert [p.id for p in merged] == ["2", "1"]


def test_populate_rg_compagnie_inserts_commandement_header():
    rows = [_row(sub="BRIGADE TERRITORIALE DE BACONGO", id="1")]
    layout = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="rg",
                order=0,
                structure_code="RG-BZV",
                subdivision_order=[
                    "COMPAGNIE DE GENDARMERIE TERRITORIALE BRAZZAVILLE 1",
                    "BRIGADE TERRITORIALE DE BACONGO",
                ],
            )
        ]
    )
    sections, _, _ = populate_liste_sections(
        layout, rows, _st("RG-BZV"), include_orphans=False
    )
    blocks = sections[0][3]
    labels = [lbl for lbl, _ in blocks]
    assert RG_COMMANDEMENT_MARKER in labels
    seq = iter_section_rubrics(sections[0][0], sections[0][1], blocks)
    sec2_labels = [p for k, p in seq if k == "sec2"]
    assert "COMMANDEMENT" in sec2_labels


def test_populate_orphans_at_end():
    rows = [_row(structure="INCONNU", sub="SUB X", id="99")]
    layout = ListeLayoutConfig(
        sections=[
            SectionBlock(id="a", order=0, structure_code="COMGEND", subdivision_order=["Direction"])
        ]
    )
    sections, missing, assigned = populate_liste_sections(
        layout, rows, _st("COMGEND"), include_orphans=True
    )
    assert missing == [("INCONNU", "SUB X", 1)]
    assert "id:99" in assigned
    assert any("HORS CONFIGURATION" in s[0] for s in sections)


def test_populate_no_duplicate_assignment():
    rows = [_row(structure="RG-BZV", sub="COMMANDEMENT", id="1")]
    layout = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="rg",
                order=0,
                structure_code="RG-BZV",
                subdivision_order=["COMMANDEMENT RG-BZV", "ETAT-MAJOR RG-BZV"],
            )
        ]
    )
    sections, _, assigned = populate_liste_sections(
        layout, rows, _st("RG-BZV"), include_orphans=False
    )
    total = sum(len(p) for _, _, _, bl in sections for _, p in bl)
    assert total == 1
    assert len(assigned) == 1


def test_comgend_direction_fonction_sort():
    rows = [
        _row(
            structure="COMGEND",
            sub="Direction",
            id="1",
            fonction="DIRECTEUR DDT",
            ordre=1,
        ),
        _row(
            structure="COMGEND",
            sub="Direction",
            id="2",
            fonction="INSPECTEUR IT",
            ordre=2,
            nom="INSPECTEUR",
        ),
    ]
    layout = ListeLayoutConfig(
        sections=[
            SectionBlock(
                id="c",
                order=0,
                structure_code="COMGEND",
                subdivision_order=["Direction"],
            )
        ]
    )
    sections, _, _ = populate_liste_sections(
        layout, rows, _st("COMGEND"), include_orphans=False
    )
    people = sections[0][3][0][1]
    assert [p.id for p in people] == ["2", "1"]
