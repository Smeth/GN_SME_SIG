"""Ajout direct d'un diplôme depuis la cellule SME, sans ouvrir le dossier."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSortFilterProxyModel
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QApplication, QDialog

from sme_gend.ui.views.edition_sme_view import EditionSmeView, _COL_IDX, _COLS

app = QApplication.instance() or QApplication([])


def test_click_diplome_respects_proxy_sorting():
    model = QStandardItemModel(2, len(_COLS))
    model.setData(model.index(0, 0), "B")
    model.setData(model.index(1, 0), "A")
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.sort(0)
    rows = [SimpleNamespace(id="B"), SimpleNamespace(id="A")]
    view = SimpleNamespace(
        _proxy=proxy, _model=SimpleNamespace(_rows=rows), _add_diplome=Mock(),
        _open_full_dossier=Mock(), _get_selected_src_rows=Mock(return_value=[1]),
    )
    index = proxy.index(0, _COL_IDX["diplome"])
    EditionSmeView._on_table_click(view, index)
    view._add_diplome.assert_called_once_with(rows[1])
    EditionSmeView._on_table_double_click(view, index)
    view._open_full_dossier.assert_not_called()
    EditionSmeView._on_table_click(view, proxy.index(0, _COL_IDX["noms"]))
    assert view._add_diplome.call_count == 1


@pytest.mark.parametrize("accepted,editable", [(True, True), (False, True), (True, False)])
def test_add_diplome_saves_and_refreshes_only_when_allowed(monkeypatch, accepted, editable):
    from sme_gend.services import agent_dossier_service, sensitive_data_policy
    from sme_gend.ui import diplome_edit_dialog

    service = Mock()
    dialog = Mock()
    dialog.exec.return_value = (
        QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
    )
    dialog.values.return_value = {"libelle": "Nouveau diplôme"}
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(diplome_edit_dialog, "DiplomeEditDialog", factory)
    monkeypatch.setattr(agent_dossier_service, "AgentDossierService", Mock(return_value=service))
    monkeypatch.setattr(sensitive_data_policy, "assert_may_mutate_dossier_history", Mock())
    view = SimpleNamespace(
        _settings=object(), _apply_editing_lock=Mock(),
        _require_editable=Mock(return_value=editable), _sync_liste_rows_from_db=Mock(),
    )
    row = SimpleNamespace(id="42", noms="Agent test")
    EditionSmeView._add_diplome(view, row)
    if accepted and editable:
        service.add_diplome.assert_called_once_with(row, libelle="Nouveau diplôme")
        view._sync_liste_rows_from_db.assert_called_once_with(["42"])
    else:
        service.add_diplome.assert_not_called()
        view._sync_liste_rows_from_db.assert_not_called()
    if not editable:
        factory.assert_not_called()
