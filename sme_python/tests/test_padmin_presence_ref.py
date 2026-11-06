"""Présence : libellés explicites, stage distinct et autres choix du référentiel."""

from sme_gend.models.padmin_ref import PadminRefRow
from sme_gend.padmin_present import (
    PadminPresenceIndex,
    padmin_counts_as_present,
)


def _index() -> PadminPresenceIndex:
    return PadminPresenceIndex.from_rows(
        [
            PadminRefRow(libelle="Présent au corps", compte_comme_present=True, ordre_tri=1),
            PadminRefRow(libelle="Mission", compte_comme_present=False, ordre_tri=2),
            PadminRefRow(libelle="Présent mission", compte_comme_present=False, ordre_tri=3),
        ]
    )


def test_ref_present_without_mot_present():
    idx = _index()
    assert padmin_counts_as_present("Présent au corps", "present", index=idx)
    assert not padmin_counts_as_present("Mission", "present", index=idx)


def test_ref_excludes_text_present_if_flag_false():
    idx = _index()
    assert not padmin_counts_as_present("Présent mission", "present", index=idx)


def test_empty_padmin_is_present_au_corps():
    idx = _index()
    assert padmin_counts_as_present(None, "present", index=idx)
    assert padmin_counts_as_present("", "present", index=idx)
    assert padmin_counts_as_present("   ", "present", index=idx)


def test_mission_code_not_present():
    idx = _index()
    assert not padmin_counts_as_present("Mission", "present", index=idx)


def test_ref_false_overridden_when_label_is_present():
    idx = PadminPresenceIndex.from_rows(
        [
            PadminRefRow(libelle="Présent", compte_comme_present=False, ordre_tri=1),
        ]
    )
    assert padmin_counts_as_present("Présent", "present", index=idx)


def test_stage_interne_is_distinct_from_present():
    idx = PadminPresenceIndex.from_rows(
        [
            PadminRefRow(libelle="StageInterne", compte_comme_present=False, ordre_tri=2),
        ]
    )
    assert not padmin_counts_as_present("StageInterne", "present", index=idx)
    assert padmin_counts_as_present("StageInterne", "stage_interne", index=idx)
    assert padmin_counts_as_present("StageInterne", "les_deux", index=idx)
    from sme_gend.padmin_present import infer_compte_comme_present

    assert infer_compte_comme_present("")
    assert infer_compte_comme_present("Présent")
    assert not infer_compte_comme_present("Stage interne")
    assert not infer_compte_comme_present("Mission")
    assert not infer_compte_comme_present("Présent mission")
    assert not infer_compte_comme_present("Malade")


def test_fallback_text_when_not_in_ref():
    idx = _index()
    assert padmin_counts_as_present("Agent présent spécial", "present", index=idx)


def test_stage_interne_unchanged():
    idx = _index()
    assert padmin_counts_as_present("Stage interne", "stage_interne", index=idx)
    assert padmin_counts_as_present("Présent au corps", "les_deux", index=idx)


def test_padmin_where_clause_never_uses_none_column():
    from sme_gend.repositories.mysql_agent_repository import _padmin_where_clause

    for mode in ("present", "stage_interne", "les_deux"):
        sql, _ = _padmin_where_clause(mode)
        assert "None" not in sql
        assert "COALESCE(rpa.libelle" in sql
