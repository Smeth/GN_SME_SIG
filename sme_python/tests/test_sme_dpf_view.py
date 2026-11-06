"""Configuration de la vue d'édition SME DPF."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView

from sme_gend.config.settings import Settings
from sme_gend.services.sme_dpf_scope import row_in_sme_dpf_scope
from sme_gend.ui.views.edition_sme_view import _EditionFilterProxy
from sme_gend.ui.views.sme_dpf_view import SmeDpfView


app = QApplication.instance() or QApplication([])


def test_sme_dpf_view_initializes_with_its_configuration() -> None:
    view = SmeDpfView(Settings(database_path=Path(".")))

    assert view._view_title.startswith("SME DPF")
    assert view._edit_permission == "effectifs.sme_dpf"
    assert view._settings_app == "EditionDPF"
    assert view._qsettings_key("compact") == "edition_dpf/compact"
    assert view._progress_title == "SME DPF"
    assert view._default_new_structure == "DPF"
    assert view._row_scope is row_in_sme_dpf_scope
    assert view._proxy._structure_view_filter is not None
    assert view._proxy._custom_structure_view
    assert view._lbl_structure_filter.text() == "Vue :"
    assert view._act_import_csv is not None

    view.close()


def test_edition_proxy_uses_visible_row_numbers() -> None:
    proxy = _EditionFilterProxy()

    assert proxy.headerData(0, Qt.Orientation.Vertical) == "1"
    assert proxy.headerData(14, Qt.Orientation.Vertical) == "15"


def test_sme_dpf_editing_lock_uses_sme_dpf_permission_not_global_edition() -> None:
    view = SmeDpfView(Settings(database_path=Path(".")))

    with patch(
        "sme_gend.services.auth_guard.session_has_permission",
        side_effect=lambda perm: perm == "effectifs.sme_dpf",
    ), patch(
        "sme_gend.services.sme_editing_policy.evaluate_editing_state_for_settings",
        return_value=type("St", (), {"locked": False, "tabsme_statut": None, "detail": ""})(),
    ):
        view._apply_editing_lock()

    assert view._auth_read_only is False
    assert view._editing_locked is False
    assert view._table.editTriggers() != QAbstractItemView.EditTrigger.NoEditTriggers

    view.close()
