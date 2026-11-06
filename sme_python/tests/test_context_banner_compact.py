from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent

from sme_gend.config.settings import Settings
from sme_gend.ui.context_banner import ContextBanner
from sme_gend.ui.theme import apply_app_theme

_APP = None

@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [960, 1536])
def test_context_stays_on_one_line(monkeypatch, tmp_path, theme, width):
    global _APP
    app = QApplication.instance() or QApplication([])
    _APP = app
    apply_app_theme(app, theme)
    monkeypatch.setattr(ContextBanner, 'refresh', lambda self: None)
    banner = ContextBanner(Settings(database_path=Path('unused')))
    labels = (banner._lbl_mois, banner._lbl_source, banner._lbl_mysql,
              banner._lbl_lock, banner._lbl_hint)
    banner._lbl_mois.setText('Période : Septembre 2026')
    banner._lbl_source.setText('Source : MySQL · mois affiché')
    banner.set_mysql_status(True, '127.0.0.1:3306/smegend')
    banner._lbl_lock.setText('Saisie bloquée — Fermé')
    banner.set_filter_available(True)
    back, forward = Mock(), Mock()
    banner.set_page_nav_handlers(back, forward)
    banner.set_page_nav_enabled(back=True, forward=True)
    banner.resize(width, banner.sizeHint().height())
    banner.show()
    app.processEvents()
    assert banner.width() == width
    assert banner.height() < 60
    assert len({label.geometry().center().y() for label in labels}) == 1
    assert all(not label.wordWrap() for label in labels)
    banner._btn_nav_back.click()
    banner._btn_nav_forward.click()
    back.assert_called_once()
    forward.assert_called_once()
    assert '127.0.0.1' in banner._lbl_mysql.toolTip()
    banner.grab().save(str(tmp_path / f'banner_{theme}_{width}.png'))
    banner.close()
    banner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
