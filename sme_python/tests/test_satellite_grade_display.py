"""Grade abbreviations in satellite tables, using synthetic reference data."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget

from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.services.personnel_list_order import GradeSortHelper
from sme_gend.ui.grade_sort_table_item import GradeSortTableWidgetItem


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def helper():
    return GradeSortHelper([
        GradeRefRow("CNE", "CAPITAINE", 5, None),
        GradeRefRow("COL", "COLONEL", 2, None),
    ])


@pytest.mark.parametrize("raw,expected", [
    ("CAPITAINE", "CNE"), (" capitaine ", "CNE"),
    ("cne", "CNE"), ("COLONEL", "COL"),
    (None, ""), ("", ""), ("GRADE INCONNU", "GRADE INCONNU"),
])
def test_shared_grade_item_displays_reference_code(app, raw, expected):
    assert GradeSortTableWidgetItem(raw, helper()).text() == expected


def test_grade_items_keep_military_precedence(app):
    grades = helper()
    colonel = GradeSortTableWidgetItem("COLONEL", grades)
    capitaine = GradeSortTableWidgetItem("CAPITAINE", grades)
    assert colonel < capitaine


@pytest.mark.parametrize("kind", ["conge", "desertion", "mission", "malade", "punis"])
def test_actual_satellite_tables_show_codes_without_mutating_rows(app, kind):
    from sme_gend.ui.views.conges_view import CongesView
    from sme_gend.ui.views.desertion_view import DesertionView
    from sme_gend.ui.views.missions_view import MissionsView
    from sme_gend.ui.views.malade_punis_view import MaladePunisView

    row = SimpleNamespace(
        id="synthetic", noms="Agent test", grade="CAPITAINE", structure="SYN",
        position_admin="Test", obs=None, satellite_join_key=lambda: "synthetic",
    )
    table = QTableWidget(0, 10)
    view = SimpleNamespace(
        _ensure_grade_sort=helper, _tbl=table, _rows=[row], _checked=set(),
        _row_key=lambda r: r.id, _details={},
        _on_select=MagicMock(), _update_checked_label=MagicMock(),
        _tbl_malade=table, _malade_rows=[row], _malade_checked=set(),
        _malade_detail_for_row=lambda r: None,
        _clear_malade_form=MagicMock(), _update_malade_checked_label=MagicMock(),
        _tbl_punis=table, _punis_rows=[row], _punis_checked=set(),
        _on_punis_select=MagicMock(), _update_punis_checked_label=MagicMock(),
        _movement_key=lambda m: m.liste.id,
        _display_rows=[SimpleNamespace(liste=row, conge=None, elapsed_days=None, is_long_alert=False)],
    )
    fill = {
        "conge": CongesView._fill_table,
        "desertion": DesertionView._fill_table,
        "mission": MissionsView._fill_table,
        "malade": MaladePunisView._fill_malade_table,
        "punis": MaladePunisView._fill_punis_table,
    }[kind]
    fill(view)
    assert table.item(0, 2).text() == "CNE"
    assert row.grade == "CAPITAINE"
