"""Régressions d'édition et de rendu du tableau Malades / Punis."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QWidget

from sme_gend.models.numerique_satellite import MaladeDetail
from sme_gend.ui.padmin_satellite_dialog import SatelliteFormResult
from sme_gend.ui.views.malade_punis_view import MaladePunisView


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_malade_and_punis_tables_do_not_open_inline_text_editors(qapp):
    view = MaladePunisView.__new__(MaladePunisView)
    QWidget.__init__(view)
    view._malade_checked = set()
    view._punis_checked = set()
    view._malade_rows = []
    view._punis_rows = []
    view._can_edit = True
    view._test_tabs = [view._build_malade_tab(), view._build_punis_tab()]

    assert view._tbl_malade.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert view._tbl_punis.editTriggers() == QTableWidget.EditTrigger.NoEditTriggers
    assert view._btn_p_reset.text() == "Remettre à Présent (cochés)"
    assert "cochées" in view._btn_p_reset.toolTip() or "coch" in view._btn_p_reset.toolTip()


@pytest.mark.parametrize("reload_fails", [False, True])
def test_double_click_malade_opens_and_saves_edit_dialog(qapp, monkeypatch, reload_fails):
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.ui import padmin_satellite_dialog

    row = ListeGeneraleRow(
        id="agent-1",
        noms="AGENT UN",
        date_naissance=None,
        lieu_naissance=None,
        grade="GND",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Malade",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    view = MaladePunisView.__new__(MaladePunisView)
    QWidget.__init__(view)
    view._malade_checked = set()
    view._punis_checked = set()
    view._malade_rows = []
    view._punis_rows = []
    view._can_edit = True
    view._test_tab = view._build_malade_tab()
    view._tbl_malade.setRowCount(1)
    key_item = QTableWidgetItem("agent-1")
    key_item.setData(Qt.ItemDataRole.UserRole, "agent-1")
    view._tbl_malade.setItem(0, 0, key_item)
    name_item = QTableWidgetItem(row.noms)
    view._tbl_malade.setItem(0, 1, name_item)
    view._malade_rows = [row]
    existing = MaladeDetail("01/10/2026", "Ancienne valeur", None)
    view._malade_details = {"agent-1": existing}
    view._sat = MagicMock()
    view._check_can_edit = lambda **_kwargs: True
    view._on_malade_select = MagicMock()
    updated = MaladeDetail("02/10/2026", "Nouvelle valeur", "Observation")
    view._sat.load_malades.return_value = {"agent-1": updated}
    warning = MagicMock()
    monkeypatch.setattr("sme_gend.ui.views.malade_punis_view.QMessageBox.warning", warning)
    if reload_fails:
        view._sat.load_malades.side_effect = RuntimeError("synthetic reload failure")
    dialog_calls = []

    def run_dialog(parent, **kwargs):
        dialog_calls.append((parent, kwargs))
        # A background refresh during the modal must not replace the snapshot
        # associated with the fields the user actually edited.
        view._malade_details = {"agent-1": MaladeDetail("03/10/2026", "OTHER POST", None)}
        return SatelliteFormResult(kind="malade", malade=updated)

    monkeypatch.setattr(padmin_satellite_dialog.PadminSatelliteDialog, "run", run_dialog)

    view._tbl_malade.itemDoubleClicked.emit(name_item)

    assert dialog_calls == [
        (
            view,
            {
                "kind": "malade",
                "agent_label": "AGENT UN",
                "malade": existing,
            },
        )
    ]
    view._sat.upsert_malade.assert_called_once_with("agent-1", updated, expected=existing)
    if reload_fails:
        warning.assert_called_once()
        assert "Enregistrement effectué" in warning.call_args.args[-1]
        view._on_malade_select.assert_not_called()
    else:
        assert view._malade_details["agent-1"] == updated
        view._on_malade_select.assert_called_once_with()


def test_malade_selection_uses_loaded_details_without_database_reload(qapp):
    from sme_gend.models.liste_generale import ListeGeneraleRow
    from sme_gend.models.grade_ref import GradeRefRow
    from sme_gend.services.personnel_list_order import GradeSortHelper

    view = MaladePunisView.__new__(MaladePunisView)
    QWidget.__init__(view)
    view._grade_sort = GradeSortHelper([GradeRefRow("GND", "GENDARME", 10, None)])
    view._malade_checked = set()
    view._punis_checked = set()
    view._malade_rows = []
    view._punis_rows = []
    view._can_edit = True
    view._test_tab = view._build_malade_tab()
    row = ListeGeneraleRow(
        id="agent-1",
        noms="AGENT UN",
        date_naissance=None,
        lieu_naissance=None,
        grade="GENDARME",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Malade",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="U001",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )
    view._malade_rows = [row]
    view._malade_details = {"agent-1": MaladeDetail("01/10/2026", "Grippe", None)}
    view._sat = MagicMock()
    view._tbl_malade.setRowCount(1)
    key_item = QTableWidgetItem("agent-1")
    key_item.setData(Qt.ItemDataRole.UserRole, "agent-1")
    view._tbl_malade.setItem(0, 0, key_item)
    view._tbl_malade.selectRow(0)

    view._on_malade_select()

    view._sat.load_malades.assert_not_called()
    assert view._m_path.text() == "Grippe"
    assert view._lbl_malade_grade.text() == "GND"


def test_malade_satellite_load_error_is_visible_and_retryable(qapp, monkeypatch):
    from sme_gend.ui.views import malade_punis_view
    from sme_gend.ui.view_lifecycle import ViewDataState

    view = MaladePunisView.__new__(MaladePunisView)
    QWidget.__init__(view)
    view._malade_checked = set()
    view._punis_checked = set()
    view._malade_rows = []
    view._punis_rows = []
    view._can_edit = True
    view._view_data = ViewDataState(stale=True)
    view._liste = MagicMock()
    view._liste.fetch_all.return_value = []
    view._sat = MagicMock()
    view._sat.load_malades.side_effect = RuntimeError("lecture satellite impossible")
    view._settings = MagicMock()
    warnings = []

    def run_inline(_parent, work, _done, *, on_error=None):
        try:
            work()
        except Exception as exc:
            on_error(exc)

    monkeypatch.setattr(malade_punis_view, "run_in_background", run_inline)
    monkeypatch.setattr(
        malade_punis_view.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    view._reload_lists_async()

    assert len(warnings) == 1
    assert "lecture satellite impossible" in warnings[0][-1]
    assert view._view_data.begin_load()
