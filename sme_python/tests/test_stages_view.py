"""Tests filtres stages — positions admin et périmètre mois."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import sys
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QWidget,
)

from sme_gend.config.liste_generale_scope import settings_for_chosen_export_mois
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.views.stages_view import (
    _editable_fiche_nature_combo,
    _is_any_stage_row,
    _is_stage_ex,
    _is_stage_interne,
    _is_stage_local,
    _parse_ym,
    padmin_for_stage_kind,
    stage_kind_label,
)


def _row(padmin: str) -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="1",
        noms="Test",
        date_naissance=None,
        lieu_naissance=None,
        grade="GND",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
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


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_stage_row_filters() -> None:
    assert _is_stage_local(_row("StageLocal"))
    assert _is_stage_ex(_row("StageEx"))
    assert _is_stage_interne(_row("Stage interne"))
    assert _is_stage_interne(_row("StageInterne"))
    assert _is_stage_interne(_row(" STAGE   INTERNE "))
    assert _is_stage_local(_row("Stage Local"))
    assert not _is_stage_local(_row("Présent"))


def test_settings_for_chosen_export_mois_stages_view() -> None:
    base = replace(Settings(database_path=Path(".")), mysql_view_mois=None)
    scoped = settings_for_chosen_export_mois(base, "2026-05")
    assert scoped.mysql_view_mois == "2026-05"


def test_stages_scoped_settings_pin_view_mois() -> None:
    from dataclasses import replace

    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm

    base = replace(Settings(database_path=Path(".")), mysql_view_mois=None)
    ym = canonical_sme_mois_yyyy_mm("2026-05")
    scoped = replace(base, mysql_view_mois=ym)
    assert scoped.mysql_view_mois == "2026-05"


def test_parse_ym() -> None:
    assert _parse_ym("2026-07") == (2026, 7)


def test_padmin_for_stage_kind() -> None:
    assert padmin_for_stage_kind("local") == "StageLocal"
    assert padmin_for_stage_kind("ex") == "StageEx"
    assert padmin_for_stage_kind("interne") == "Stage interne"
    assert stage_kind_label("local") == "Stage local"
    assert _is_any_stage_row(_row("StageLocal"))
    assert not _is_any_stage_row(_row("Présent"))


def test_stage_table_select_all_and_name_is_read_only(qapp):
    from sme_gend.services.personnel_list_order import GradeSortHelper
    from sme_gend.ui.views.stages_view import StagesView

    row_one = _row("StageInterne")
    row_one.id = "101"
    row_one.noms = "AGENT UN"
    row_two = _row("Stage interne")
    row_two.id = "102"
    row_two.noms = "AGENT DEUX"
    view = StagesView.__new__(StagesView)
    view._rows_interne = [row_one, row_two]
    view._rows_interne_all = [row_one, row_two]
    view._checked = {"local": set(), "ex": set(), "interne": set()}
    view._grade_sort = GradeSortHelper([])
    view._tbl_interne = QTableWidget()
    view._tbl_interne.setColumnCount(9)
    view._details_interne = {}
    view._lbl_interne_checked = QLabel()
    view._btn_interne_end = QPushButton()
    view._btn_interne_create = QPushButton()
    view._btn_interne_select_all = QPushButton()
    view._can_edit = True
    view._grade_rows = []
    from sme_gend.ui.widgets.date_input import FrenchDateEdit

    view._f_interne_nature = _editable_fiche_nature_combo()
    view._f_interne_lieu = QLineEdit()
    view._f_interne_reference = QLineEdit()
    view._f_interne_session = QLineEdit()
    view._f_interne_deb = FrenchDateEdit()
    view._f_interne_fin = FrenchDateEdit()

    view._fill_table("interne")
    assert view._tbl_interne.rowCount() == 2
    assert not view._tbl_interne.item(0, 2).flags() & Qt.ItemFlag.ItemIsEditable
    assert not view._tbl_interne.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable

    view._toggle_select_all("interne")
    assert view._checked["interne"] == {"101", "102"}
    assert view._btn_interne_select_all.text() == "Tout décocher"
    view._toggle_select_all("interne")
    assert view._checked["interne"] == set()
    assert view._btn_interne_select_all.text() == "Tout cocher"

def test_stage_tables_disable_inline_editing(qapp):
    from sme_gend.ui.views.stages_view import StagesView

    view = StagesView.__new__(StagesView)
    QWidget.__init__(view)
    view._test_tabs = [
        view._build_stage_tab("local"),
        view._build_stage_tab("ex"),
        view._build_interne_tab(),
    ]

    assert view._tbl_local.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert view._tbl_ex.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert view._tbl_interne.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers


@pytest.mark.parametrize(
    "failed_loader",
    ["load_stages_loc", "load_stages_ex", "load_stages_internal"],
)
def test_stage_satellite_load_error_is_visible_and_retryable(qapp, monkeypatch, failed_loader):
    from sme_gend.ui.view_lifecycle import ViewDataState
    from sme_gend.ui.views import stages_view

    view = stages_view.StagesView.__new__(stages_view.StagesView)
    QWidget.__init__(view)
    view._view_data = ViewDataState(stale=True)
    view._load_generation = 0
    view._view_mois = None
    view._liste = MagicMock()
    view._liste.fetch_all.return_value = []
    view._sat = MagicMock()
    view._sat.load_stages_internal.return_value = {}
    getattr(view._sat, failed_loader).side_effect = RuntimeError("lecture satellite impossible")
    view._settings = MagicMock()
    warnings = []

    def run_inline(_parent, work, _done, *, on_error=None):
        try:
            work()
        except Exception as exc:
            on_error(exc)

    monkeypatch.setattr(stages_view, "run_in_background", run_inline)
    monkeypatch.setattr(
        stages_view.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    view._reload_lists_async()

    assert len(warnings) == 1
    assert "lecture satellite impossible" in warnings[0][-1]
    assert view._view_data.begin_load()
