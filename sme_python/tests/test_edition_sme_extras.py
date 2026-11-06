"""Tests helpers Edition SME (filtres proxy, import CSV)."""

from __future__ import annotations

from pathlib import Path

from sme_gend.models.grade_ref import GradeRefRow
from PySide6.QtCore import Qt

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.edition_sme_support import import_csv_updates
from sme_gend.ui.personnel_list_filters import grade_matches_filter
from sme_gend.ui.views.edition_sme_view import (
    _COL_IDX,
    _EditionFilterProxy,
    _EditionModel,
    _edition_count_label,
    _table_sort_order_code,
    _table_sort_order_from_code,
)

_GRADES = [
    GradeRefRow(code="COL", libelle="Colonel", pref_ord=1, effectif_g=None),
    GradeRefRow(code="MDL", libelle="Maréchal des logis", pref_ord=2, effectif_g=None),
]


def _row(
    row_id: str,
    *,
    mat_mil: str | None = None,
    mat_sol: str | None = None,
    padmin: str | None = "Présent",
    grade: str = "MDL",
) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id=row_id,
        noms=f"Agent {row_id}",
        date_naissance=None,
        lieu_naissance=None,
        grade=grade,
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=mat_mil,
        matricule_solde=mat_sol,
        position_admin=padmin,
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


class _FakePersonnel:
    def __init__(self) -> None:
        self.updates: list[tuple[ListeGeneraleRow, ListeGeneraleRow]] = []

    def update_personnel(self, before: ListeGeneraleRow, after: ListeGeneraleRow) -> None:
        self.updates.append((before, after))


def test_proxy_filter_grade_resolves_libelle() -> None:
    model = _EditionModel()
    rows = [
        _row("1", grade="Colonel"),
        _row("2", grade="MDL"),
        _row("3", grade="Maréchal des logis"),
    ]
    model.load(rows, None, ["COL", "MDL"], [], [], [], grade_rows=_GRADES)
    proxy = _EditionFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_grade("COL", combo_display="COL — Colonel")
    assert proxy.rowCount() == 1
    src = proxy.mapToSource(proxy.index(0, 0)).row()
    assert model._rows[src].id == "1"


def test_grade_matches_filter_with_referentiel() -> None:
    assert grade_matches_filter("Colonel", "COL", combo_display="COL — Colonel", grades=_GRADES)
    assert not grade_matches_filter("MDL", "COL", grades=_GRADES)


def test_proxy_filters_no_matricule_and_presents() -> None:
    model = _EditionModel()
    rows = [
        _row("1", mat_mil="111", padmin="Présent"),
        _row("2", mat_mil=None, padmin="Présent"),
        _row("3", mat_mil=None, padmin="Détaché"),
    ]
    model.load(rows, None, [], [], [], [])
    proxy = _EditionFilterProxy()
    proxy.setSourceModel(model)

    proxy.set_no_mat_mil(True)
    assert proxy.rowCount() == 2

    proxy.set_no_mat_mil(False)
    proxy.set_presents_only(True)
    assert proxy.rowCount() == 2


def test_import_csv_updates_by_id(tmp_path: Path) -> None:
    before = _row("10", mat_mil="M10")
    rows_by_id = {"10": before}
    svc = _FakePersonnel()
    csv_path = tmp_path / "updates.csv"
    csv_path.write_text(
        "N°;Nom et prénom;Structure\n10;DUPONT Jean;U002\n",
        encoding="utf-8-sig",
    )
    result = import_csv_updates(svc, rows_by_id, csv_path, match_field="id")
    assert result.updated == 1
    assert len(svc.updates) == 1
    _, after = svc.updates[0]
    assert after.noms == "DUPONT Jean"
    assert after.structure == "U002"
    assert rows_by_id["10"].structure == "U002"


def test_edition_count_label() -> None:
    assert _edition_count_label(0, 0) == "Aucun personnel chargé"
    assert _edition_count_label(12, 100) == "12 affiché(s) sur 100 personnels"
    assert _edition_count_label(50, 50) == "50 personnel(s)"


def test_undo_peek_restore_and_session_log() -> None:
    model = _EditionModel()
    row = _row("1", grade="MDL")
    svc = _FakePersonnel()
    model.load([row], None, [], [], [], [], personnel=svc)
    col = _COL_IDX["grade"]
    idx = model.index(0, col)
    assert model.setData(idx, "COL", Qt.ItemDataRole.EditRole)
    assert model._rows[0].grade == "COL"
    peek = model.peek_undo()
    assert peek is not None
    assert peek.old_val == "MDL"
    assert peek.new_val == "COL"
    assert model.active_undo_count() == 1

    msg = model.undo_last()
    assert msg is not None
    assert "MDL" in msg
    assert model._rows[0].grade == "MDL"
    assert model.active_undo_count() == 0
    assert len(model._session_log) == 1
    assert model._session_log[0].active is False


def test_undo_all_session() -> None:
    model = _EditionModel()
    row = _row("1", grade="MDL")
    svc = _FakePersonnel()
    model.load([row], None, [], [], [], [], personnel=svc)
    col_g = _COL_IDX["grade"]
    col_s = _COL_IDX["structure"]
    model.setData(model.index(0, col_g), "COL", Qt.ItemDataRole.EditRole)
    model.setData(model.index(0, col_s), "U002", Qt.ItemDataRole.EditRole)
    assert model.active_undo_count() == 2
    ok, errors = model.undo_all_session()
    assert errors == []
    assert ok == 2
    assert model._rows[0].grade == "MDL"
    assert model._rows[0].structure == "U001"


def test_cell_tooltip_shows_previous_value() -> None:
    model = _EditionModel()
    row = _row("1", grade="MDL")
    svc = _FakePersonnel()
    model.load([row], None, [], [], [], [], personnel=svc)
    col = _COL_IDX["grade"]
    model.setData(model.index(0, col), "COL", Qt.ItemDataRole.EditRole)
    tip = model.data(model.index(0, col), Qt.ItemDataRole.ToolTipRole)
    assert tip is not None
    assert "MDL" in tip
    assert "COL" in tip


def test_table_sort_order_serialization_avoids_int_on_qt_enum() -> None:
    assert _table_sort_order_code(Qt.SortOrder.AscendingOrder) == 0
    assert _table_sort_order_code(Qt.SortOrder.DescendingOrder) == 1
    assert _table_sort_order_from_code(0) == Qt.SortOrder.AscendingOrder
    assert _table_sort_order_from_code(1) == Qt.SortOrder.DescendingOrder
    assert (
        _table_sort_order_from_code(Qt.SortOrder.DescendingOrder)
        == Qt.SortOrder.DescendingOrder
    )
