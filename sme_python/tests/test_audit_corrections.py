"""Régressions de l'audit : identités synthétiques, aucun accès aux données réelles."""
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QTabWidget, QVBoxLayout, QWidget

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import PersonnelSearchHit
from sme_gend.services.career_import_alerts import CareerAlert, CareerAcknowledgement
from sme_gend.ui.view_lifecycle import ViewDataState


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    return application


@pytest.fixture
def settings():
    return Settings(database_path=Path("unused.mdb"), mysql_view_mois="2026-09")


def person(key, name, **kwargs):
    data = {f.name: None for f in fields(ListeGeneraleRow) if f.name != "access_profile_fields"}
    return ListeGeneraleRow(**(data | dict(id="career-" + key, access_join_id=key, noms=name) | kwargs))


@pytest.mark.parametrize("order", [Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder])
def test_search_opens_the_sorted_identity_and_shows_disambiguating_fields(app, settings, order):
    from sme_gend.ui.global_search_dialog import GlobalSearchDialog
    hits = [PersonnelSearchHit("active", person("Z", "AGENT ZULU", date_naissance=datetime(1980, 1, 2), matricule_solde="S-Z")),
            PersonnelSearchHit("historical", person("A", "AGENT ALPHA", date_naissance=datetime(1981, 2, 3), matricule_solde="S-A"))]
    opened = MagicMock()
    view = GlobalSearchDialog(settings, MagicMock(), opened)
    view._hits = hits
    view._fill_table(hits)
    view._table.sortItems(2, order)
    view._table.selectRow(0)
    visible_name = view._table.item(0, 2).text()
    visible_id = view._table.item(0, 6).text()
    view._on_activate()
    hit = opened.call_args.args[0]
    assert hit.row.noms == visible_name
    assert hit.row.satellite_join_key() == visible_id
    assert view._table.item(0, 7).text() == hit.row.date_naissance.strftime("%d/%m/%Y")
    assert view._table.item(0, 8).text() == hit.row.matricule_solde
    view.close()


@pytest.mark.parametrize("kind", ["missions", "desertion", "conges"])
@pytest.mark.parametrize("change", ["month", "configuration", "invalidate"])
def test_old_satellite_response_or_error_cannot_replace_the_current_context(app, settings, monkeypatch, kind, change):
    from sme_gend.ui.views import missions_view, desertion_view, conges_view
    module, cls = {"missions": (missions_view, missions_view.MissionsView),
                   "desertion": (desertion_view, desertion_view.DesertionView),
                   "conges": (conges_view, conges_view.CongesView)}[kind]
    view = SimpleNamespace(_view_data=ViewDataState(), _liste=MagicMock(), _sat=MagicMock(),
                           _settings=settings, _scoped_settings=settings, _view_mois="2026-08",
                           _rebuild_period_combos=MagicMock(), _apply_filters=MagicMock(),
                           _refresh_action_state=MagicMock(), _update_summary=MagicMock(),
                           _checked=set(), _summary=MagicMock())
    pending = []
    monkeypatch.setattr(module, "run_in_background", lambda owner, work, done, **kw: pending.append((done, kw["on_error"])))
    warning = MagicMock()
    monkeypatch.setattr(module.QMessageBox, "warning", warning)
    if kind == "conges":
        monkeypatch.setattr(module, "build_conge_movement_rows", lambda rows, *args: rows)
        monkeypatch.setattr(module, "build_conge_history_rows", lambda *args: [])
        view._movement_key = lambda row: row.satellite_join_key()
    cls._reload_lists_async(view)
    if change == "month":
        view._view_mois = "2026-09"
    elif change == "configuration":
        view._settings = replace(settings, mysql_database="synthetic_new")
    view._view_data.mark_stale()
    old = person("OLD", "ANCIEN MOIS", position_admin="Mission" if kind == "missions" else "Déserteur")
    new = person("NEW", "MOIS DEMANDÉ", position_admin=old.position_admin)
    if change == "invalidate":
        pending[0][0](([old], {}, []))
        assert not view._view_data.is_ready()
        # Missions keeps the displayed rows and nature filter until a current
        # response arrives. Other satellite lists clear their previous month.
        assert view._apply_filters.call_count == (0 if kind == "missions" else 1)
    else:
        cls._reload_lists_async(view)
        pending[1][0](([new], {}, []))
        pending[0][0](([old], {}, []))
        current = view._all_rows if kind == "conges" else view._rows_all
        assert current == [new]
        assert view._view_data.is_ready()
    pending[0][1](RuntimeError("obsolete error"))
    warning.assert_not_called()


@pytest.mark.parametrize("kind", ["missions", "desertion", "conges"])
def test_satellite_editing_is_blocked_until_the_month_is_loaded(app, monkeypatch, kind):
    from sme_gend.ui.views import missions_view, desertion_view, conges_view
    cls = {"missions": missions_view.MissionsView, "desertion": desertion_view.DesertionView,
           "conges": conges_view.CongesView}[kind]
    message = MagicMock()
    monkeypatch.setattr(QMessageBox, "information", message)
    view = SimpleNamespace(_view_data=ViewDataState())
    assert cls._check_can_edit(view) is False
    message.assert_called_once()


@pytest.fixture
def alerts_screen(app, settings, monkeypatch):
    from sme_gend.ui.views import career_alerts_view as ui
    monkeypatch.setattr(ui, "session_has_permission", lambda _: True)
    widget = ui.CareerAlertsView(settings, MagicMock(), MagicMock())
    widget._alerts = [CareerAlert("warning", "A", "AGENT HERVÉ", "2026-09", "STRUCTURE_JUMP", "Affectation vérifiée"),
                      CareerAlert("warning", "B", "AUTRE AGENT", "2026-09", "STRUCTURE_JUMP", "Autre structure")]
    widget._filter()
    yield widget
    widget.close()


def test_alert_search_handles_accents_and_preserves_the_same_occurrence(alerts_screen):
    view = alerts_screen
    view.table.selectRow(0)
    view.search.setText("herve")
    assert [a.agent_legacy_id for a in view._visible] == ["A"]
    assert view._selected().agent_legacy_id == "A"
    view.search.clear()
    assert view._selected().agent_legacy_id == "A"
    view._filter()
    assert view._selected().agent_legacy_id == "A"
    # Une version différente du constat ne récupère pas la sélection de l'ancienne.
    view._alerts[0].message = "Constat modifié"
    view._filter()
    assert view._selected() is None


def test_ack_history_search_handles_accents_and_preserves_selection(alerts_screen, monkeypatch):
    view = alerts_screen
    monkeypatch.setattr(view, "reload", lambda: None)
    view.state.setCurrentIndex(1)
    view._records = [CareerAcknowledgement("A", "STRUCTURE_JUMP", "2026-09", "fp", "opérateur", "2026-10-01", "Vérifié", "token", noms="AGENT HERVÉ")]
    view._filter_history()
    view.table.selectRow(0)
    view.search.setText("verifie")
    assert len(view._visible) == 1
    assert view._selected().agent_legacy_id == "A"


def test_runtime_settings_reach_alerts_and_deleted_personnel(alerts_screen, settings):
    from sme_gend.ui.shell_window import ShellWindow
    from sme_gend.ui.views.deleted_personnel_view import DeletedPersonnelView
    current = replace(settings, mysql_database="synthetic_new", mysql_view_mois="2026-08")
    host = SimpleNamespace(_settings=current, _personnel=SimpleNamespace(settings=current))
    old_generation = alerts_screen._generation
    ShellWindow._apply_runtime_to_view(host, alerts_screen)
    assert alerts_screen._settings is current
    assert alerts_screen._generation > old_generation
    assert alerts_screen._alerts == []
    deleted = DeletedPersonnelView.__new__(DeletedPersonnelView)
    QWidget.__init__(deleted)
    deleted._settings, deleted._panel = settings, MagicMock()
    ShellWindow._apply_runtime_to_view(host, deleted)
    assert deleted._settings is current
    deleted._panel.rebind_settings.assert_called_once_with(current)
    deleted.close()


@pytest.mark.parametrize("port", ["abc", "-1", "0", "65536", "999999"])
def test_invalid_port_is_reported_before_any_connection(app, settings, monkeypatch, port):
    from sme_gend.ui import paths_config_form as ui
    form = ui.PathsConfigForm.__new__(ui.PathsConfigForm)
    QWidget.__init__(form)
    form._mysql_port = QLineEdit(port)
    form._db = QLineEdit()
    form._build_mysql_settings = lambda: ui.PathsConfigForm._build_mysql_settings(form)
    monkeypatch.setattr("sme_gend.config.get_settings", lambda: settings)
    warning = MagicMock()
    worker = MagicMock()
    monkeypatch.setattr(ui.QMessageBox, "warning", warning)
    monkeypatch.setattr("sme_gend.ui.bg_task.run_with_progress", worker)
    form._on_test_mysql()
    warning.assert_called_once()
    worker.assert_not_called()
    form.close()


@pytest.mark.parametrize("port, expected", [("", 3306), ("1", 1), ("3306", 3306), ("65535", 65535)])
def test_valid_port_boundaries(app, port, expected):
    from sme_gend.ui.paths_config_form import PathsConfigForm
    assert PathsConfigForm._validated_mysql_port(SimpleNamespace(_mysql_port=QLineEdit(port))) == expected


@pytest.mark.parametrize("success", [False, True])
def test_settings_success_requires_a_successful_reload(app, monkeypatch, success):
    from sme_gend.ui.views.settings_view import SettingsView
    parent = QWidget()
    form = MagicMock()
    form.try_save.return_value = True
    parent._form = form
    parent.reload_runtime_configuration = MagicMock(return_value=success)
    info, warning = MagicMock(), MagicMock()
    monkeypatch.setattr(QMessageBox, "information", info)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    SettingsView._on_save(parent)
    assert info.called is success
    assert warning.called is not success
    parent.close()


def test_page_filter_shortcut_targets_the_visible_tab(app):
    from sme_gend.ui.page_search import page_search_field
    from sme_gend.ui.shell_window import ShellWindow
    page = QWidget()
    tabs = QTabWidget(page)
    layout = QVBoxLayout(page)
    layout.addWidget(tabs)
    searches = []
    for name in ("Premier", "Deuxième"):
        child = QWidget()
        field = QLineEdit("Texte", child)
        field.setPlaceholderText("Nom, grade, structure…")
        child_layout = QVBoxLayout(child)
        child_layout.addWidget(field)
        tabs.addTab(child, name)
        searches.append(field)
    page.show()
    tabs.setCurrentIndex(1)
    app.processEvents()
    assert page_search_field(page) is searches[1]
    host = SimpleNamespace(_stack=SimpleNamespace(currentIndex=lambda: 0), _page_widget=lambda _: page)
    ShellWindow._focus_current_filter(host)
    assert searches[1].selectedText() == "Texte"
    page.close()


@pytest.mark.parametrize("width", [640, 1032, 1440])
def test_banner_fits_the_available_width_and_only_advertises_available_filter(app, settings, monkeypatch, width):
    from sme_gend.ui.context_banner import ContextBanner
    monkeypatch.setattr(ContextBanner, "refresh", lambda self: None)
    banner = ContextBanner(settings)
    banner._lbl_mois.setText("Période active : Septembre 2026")
    banner._lbl_source.setText("Consultation archive : Août 2026 — mois de travail : Septembre 2026")
    banner.set_mysql_status(True, "MySQL 127.0.0.1:3306/synthetic_database")
    banner._lbl_lock.setText("Saisie bloquée — Fermé")
    banner.resize(width, 100)
    banner.show()
    app.processEvents()
    assert banner.width() == width
    assert banner.minimumSizeHint().width() <= width
    assert "Ctrl+F" not in banner._lbl_hint.text()
    banner.set_filter_available(True)
    assert "Ctrl+F" in banner._lbl_hint.text()
    for label in (banner._lbl_mois, banner._lbl_source, banner._lbl_mysql, banner._lbl_lock):
        assert label.width() > 0
    banner.close()
