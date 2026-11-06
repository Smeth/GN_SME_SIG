"""Tests ouverture fiche dossier depuis la liste générale."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.ui.personnel_filter_proxy import PersonnelFilterProxy
from sme_gend.ui.personnel_table_model import PersonnelTableModel
from sme_gend.ui.views.personnel_list_view import (
    _DOSSIER_OPEN_DEBOUNCE_MS,
    _PersonnelListTable,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _sample_row() -> ListeGeneraleRow:
    return ListeGeneraleRow(
        id="42",
        noms="DUPONT Jean",
        date_naissance=None,
        lieu_naissance=None,
        sexe="M",
        grade="GDB",
        date_grade=None,
        date_service=None,
        ndecision=None,
        matricule_militaire=None,
        matricule_solde=None,
        position_admin="Présent",
        fonction=None,
        subdivision_arme=None,
        diplome=None,
        date_obtention=None,
        structure="COMGEND",
        obs=None,
        ordre_tri=None,
        presseance=None,
    )


def test_personnel_list_table_double_click_on_name_column(qapp):
    requested: list[int] = []

    table = _PersonnelListTable(
        requested.append,
        actions_column=20,
    )
    row = _sample_row()
    model = PersonnelTableModel(rows=[row])
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(model)
    table.setModel(proxy)
    table.resize(900, 200)
    table.show()

    idx = proxy.index(0, 1)
    rect = table.visualRect(idx)
    QTest.mouseDClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        rect.center(),
    )
    qapp.processEvents()

    assert requested == [0]


def test_personnel_list_table_ignores_double_click_on_actions_column(qapp):
    requested: list[int] = []

    table = _PersonnelListTable(
        requested.append,
        actions_column=20,
    )
    row = _sample_row()
    model = PersonnelTableModel(rows=[row])
    proxy = PersonnelFilterProxy()
    proxy.setSourceModel(model)
    table.setModel(proxy)
    table.resize(900, 200)
    table.show()

    idx = proxy.index(0, 20)
    rect = table.visualRect(idx)
    QTest.mouseDClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        rect.center(),
    )
    qapp.processEvents()

    assert requested == []


def test_request_open_dossier_debounces_and_opens(qapp):
    from sme_gend.ui.views import personnel_list_view as plv_mod

    view = plv_mod.PersonnelListView.__new__(plv_mod.PersonnelListView)
    view._opening_dossier = False
    view._dossier_open_queued = False
    view._pending_dossier_row = None
    view._settings = MagicMock()
    view._referentiel = MagicMock()
    view._service = MagicMock()
    view._model = PersonnelTableModel(rows=[_sample_row()])
    view._proxy = PersonnelFilterProxy()
    view._proxy.setSourceModel(view._model)
    view._table = MagicMock()
    view._refresh_lock_banner = MagicMock()
    view._apply_loaded_rows = MagicMock()

    view._dossier_open_timer = plv_mod.QTimer()
    view._dossier_open_timer.setSingleShot(True)
    view._dossier_open_timer.timeout.connect(view._execute_pending_dossier_open)

    row = _sample_row()
    with patch.object(plv_mod, "open_personnel_dossier_dialog") as open_mock, patch.object(
        plv_mod, "evaluate_editing_state_for_settings"
    ) as st_mock, patch.object(
        plv_mod, "invalidate_liste_cache", create=True
    ):
        st_mock.return_value = MagicMock(locked=False)
        from sme_gend.ui.personnel_dossier_open import DossierOpenResult
        open_mock.return_value = DossierOpenResult(QDialog.DialogCode.Accepted, False)
        with patch(
            "sme_gend.services.liste_generale_cache.invalidate_liste_cache"
        ) as inv_mock:
            view._service.liste_complete.return_value = [row]
            view._request_open_dossier(row)
            view._request_open_dossier(row)
            view._request_open_dossier(_sample_row())
            assert view._pending_dossier_row is row
            QTest.qWait(_DOSSIER_OPEN_DEBOUNCE_MS + 40)
            open_mock.assert_called_once()
            inv_mock.assert_not_called()
            view._apply_loaded_rows.assert_not_called()
