"""Real Qt layout checks at compact and wide sizes, under both project themes."""
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QScrollArea
from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.limite_age_config import LimiteAgeConfig, GradeLimiteEntry
from sme_gend.models.soit_transmis_config import SoitTransmisConfig
from sme_gend.services.limite_age_service import LimiteAgeService
from sme_gend.ui.theme import apply_app_theme
from sme_gend.ui.views import limite_age_view as la, soit_transmis_view as st


@pytest.mark.parametrize("theme",["light","dark"])
@pytest.mark.parametrize("width,height",[(640,480),(800,600),(1100,760),(1440,900)])
@pytest.mark.parametrize("kind",["attestations","soit_transmis"])
def test_controls_fit_and_remain_reachable_after_resize(monkeypatch,tmp_path,theme,width,height,kind):
    app=QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    apply_app_theme(app,theme)
    cfg=LimiteAgeConfig(grades=[GradeLimiteEntry("LTN",55,37)])
    person=ListeGeneraleRow(**({f.name:None for f in fields(ListeGeneraleRow)} | dict(
        id="SYN-1",noms="PERSONNE FICTIVE AVEC UN NOM LONG",grade="LTN",
        date_naissance=date(1990,2,3),date_service="05/06/2010",structure="STRUCTURE FICTIVE")))
    monkeypatch.setattr(la,"can_export",lambda:True)
    monkeypatch.setattr(st,"can_export",lambda:True)
    settings=Settings(database_path=Path("synthetic-unused"))
    if kind=="attestations":
        svc=LimiteAgeService()
        view=la._AttestationsTab(settings,svc)
        view._all_rows=[(person,svc.calculate_limit(person,cfg))]
        view._fresh=True;view._stats.update_stats(view._all_rows);view._apply_filter()
        view._model.check_all();view._table.selectRow(0)
        actions=[view._btn_refresh,view._btn_zip,view._btn_print_checked,view._btn_soit_transmis,
                 view._detail._btn_docx,view._detail._btn_pdf]
    else:
        monkeypatch.setattr(st.SoitTransmisService,"load",lambda *a:SoitTransmisConfig())
        view=st.SoitTransmisView(settings)
        view._needs_reload=False
        view._model.load([person]);view._model.check_all();view._fresh=True;view._on_check_changed()
        actions=[view._btn_gen,view._btn_open,view._btn_print_all,view._btn_save_cfg]
    view.resize(width,height);view.show()
    for _ in range(4):app.processEvents()
    try:
        assert view.width()==width, (kind,theme,view.minimumSizeHint().width())
        expected=Qt.Orientation.Vertical if width<1150 else Qt.Orientation.Horizontal
        assert view._responsive_splitter.orientation()==expected
        for area in view.findChildren(QScrollArea):
            assert area.horizontalScrollBar().maximum()==0, (kind,theme,width,area.widget().minimumSizeHint())
        for action in actions:
            area=action.parentWidget()
            while area is not None and not isinstance(area,QScrollArea):area=area.parentWidget()
            assert area is not None
            area.ensureWidgetVisible(action)
            app.processEvents()
            center=action.mapTo(area.viewport(),action.rect().center())
            assert area.viewport().rect().contains(center), (kind,theme,width,action.text())
            assert action.width() >= action.minimumSizeHint().width()
        for area in view.findChildren(QScrollArea):
            area.verticalScrollBar().setValue(0)
        app.processEvents()
        view.grab().save(str(tmp_path/f"{kind}-{theme}-{width}.png"))
        view.resize(1440,900);app.processEvents()
        assert view._responsive_splitter.orientation()==Qt.Orientation.Horizontal
        assert view._model.checked_count()==1
    finally:
        view.close();view.deleteLater();app.processEvents();apply_app_theme(app,"light")


@pytest.mark.parametrize("width",[640,800,1100])
def test_full_module_tabs_do_not_force_the_window_wider(monkeypatch,width):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(LimiteAgeService,"load",lambda *a:LimiteAgeConfig())
    monkeypatch.setattr(la._StructureFormatTab,"_merge_from_referentiel",lambda *a,**kw:None)
    def fail_dialog(*args):
        raise AssertionError(str(args))
    monkeypatch.setattr(la.QMessageBox,"critical",fail_dialog)
    monkeypatch.setattr(la._RetraiteTab,"ensure_loaded",lambda *a:None)
    view=la.LimiteAgeView(Settings(database_path=Path("synthetic-unused")))
    view._grades_synced=True;view._needs_reload=False
    view.resize(width,600);view.show();app.processEvents()
    try:
        for index in range(view._tabs.count()):
            view._tabs.setCurrentIndex(index);app.processEvents()
            assert view.width()==width, view.minimumSizeHint()
    finally:
        view.close();view.deleteLater();app.processEvents()
