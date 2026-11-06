"""Parse import distinctions (CSV)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.services.distinction_excel_import import (
    DISTINCTION_KIND_MEDAILLE,
    _parse_kind,
    parse_attribution_rows,
)


def test_parse_kind_variants():
    assert _parse_kind("medaille") == DISTINCTION_KIND_MEDAILLE
    from sme_gend.models.distinction_ref import DISTINCTION_KIND_DECORATION

    assert _parse_kind("Décoration") == DISTINCTION_KIND_DECORATION


def test_parse_attribution_csv(tmp_path: Path):
    p = tmp_path / "attr.csv"
    p.write_text(
        "id_liste;type;code_distinction;date_attribution\n"
        "A1;medaille;MM;15/01/2020\n",
        encoding="utf-8",
    )
    rows = parse_attribution_rows(p)
    assert len(rows) == 1
    assert rows[0].legacy_id == "A1"
    assert rows[0].code == "MM"
