import pytest

from sme_gend.services.person_name_display import format_nom_prenoms


@pytest.mark.parametrize('raw,expected', [
    (None, ''), ('  ', ''), ('NGOUABI', 'NGOUABI'),
    ('ngouabi jean paul', 'NGOUABI Jean Paul'),
    ('NGOUABI JEAN-PAUL ÉMILE', 'NGOUABI Jean-Paul Émile'),
    ("N'GUESSAN JEAN D'ARC", "N'GUESSAN Jean D'Arc"),
    ('DE SOUZA Jean-Pierre', 'DE SOUZA Jean-Pierre'),
    ('DE SOUZA JEAN-PIERRE', 'DE SOUZA Jean-Pierre'),
    ('  NGOUABI   Jean  PAUL ', 'NGOUABI Jean Paul'),
])
def test_name_presentation_is_unicode_aware_and_idempotent(raw, expected):
    assert format_nom_prenoms(raw) == expected
    assert format_nom_prenoms(expected) == expected


def test_limite_age_table_and_details_format_without_changing_source():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from test_limite_age_print_filtered import _person
    from sme_gend.services.limite_age_service import LimiteDateResult
    from sme_gend.ui.views.limite_age_view import _PersonnelModel, _COL_NOMS
    app = QApplication.instance() or QApplication([])
    person = _person('NGOUABI JEAN PAUL')
    model = _PersonnelModel()
    model.load([(person, LimiteDateResult(None, '', None, None, None))])
    assert model.data(model.index(0, _COL_NOMS), Qt.ItemDataRole.DisplayRole) == 'NGOUABI Jean Paul'
    assert person.noms == 'NGOUABI JEAN PAUL'


@pytest.mark.parametrize('raw,family,given,expected', [
    ('MBOUNGOU MAKITA JEAN PAUL', 'MBOUNGOU MAKITA', 'Jean Paul', 'MBOUNGOU MAKITA Jean Paul'),
    ('DE LA CRUZ ÉMILE', 'DE LA CRUZ', 'Émile', 'DE LA CRUZ Émile'),
    ('MBOUNGOU MAKITA J P', 'MBOUNGOU MAKITA', 'J P', 'MBOUNGOU MAKITA J P'),
])
def test_explicit_compound_boundary_survives_serialization(raw,family,given,expected):
    from test_limite_age_print_filtered import _person
    from sme_gend.models.limite_age_config import LimiteAgeConfig
    from sme_gend.services.person_name_display import set_name_boundary, format_person_name
    person, cfg = _person(raw), LimiteAgeConfig()
    set_name_boundary(cfg,person,family,given)
    loaded = LimiteAgeConfig.from_dict(cfg.to_dict())
    assert format_person_name(person,loaded) == expected
    assert person.noms == raw


def test_boundary_cannot_rename_the_person_and_is_ignored_after_identity_change():
    from test_limite_age_print_filtered import _person
    from sme_gend.models.limite_age_config import LimiteAgeConfig
    from sme_gend.services.person_name_display import set_name_boundary, format_person_name
    person, cfg = _person('MBOUNGOU MAKITA JEAN'), LimiteAgeConfig()
    with pytest.raises(ValueError):
        set_name_boundary(cfg,person,'AUTRE','Jean')
    assert cfg.name_formats == {}
    set_name_boundary(cfg,person,'MBOUNGOU MAKITA','Jean')
    person.noms = 'AUTRE ALICE'
    assert format_person_name(person,cfg) == 'AUTRE Alice'


def test_name_format_write_requires_permission(monkeypatch):
    from unittest.mock import MagicMock
    from sme_gend.services.limite_age_service import LimiteAgeService
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission',lambda permission:False)
    service = LimiteAgeService()
    service.save = MagicMock()
    with pytest.raises(PermissionError):
        service.save_name_format(None,None,'','','unused')
    service.save.assert_not_called()


def test_compound_name_dialog_saves_explicit_fields_and_notifies_view(monkeypatch,tmp_path):
    from unittest.mock import MagicMock
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit
    from test_limite_age_print_filtered import _person
    from sme_gend.models.limite_age_config import LimiteAgeConfig
    from sme_gend.ui.views.limite_age_view import _DetailPanel
    from sme_gend.config.settings import Settings
    from pathlib import Path
    app = QApplication.instance() or QApplication([])
    # Isolated USERPROFILE has no font cache; load a real Windows font for QA.
    from PySide6.QtGui import QFontDatabase, QFont
    import os
    font = Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts/segoeui.ttf'
    if font.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0],10))
    from PySide6.QtCore import QLibraryInfo, QTranslator
    translator = QTranslator(app)
    translator.load('qtbase_fr', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    app.installTranslator(translator)
    cfg, service = LimiteAgeConfig(), MagicMock()
    service.load.return_value = cfg
    service.save_name_format.return_value = cfg
    monkeypatch.setattr('sme_gend.services.auth_guard.require_permission',lambda *args:True)
    monkeypatch.setattr('sme_gend.ui.bg_task.run_with_progress',lambda parent,title,message,work:work())
    def edit(dialog):
        family,given = dialog.findChildren(QLineEdit)
        family.setText('MBOUNGOU MAKITA')
        given.setText('Jean Paul')
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(tmp_path/'nom_prenoms.png'))
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        return dialog.result()
    monkeypatch.setattr(QDialog,'exec',edit)
    settings = Settings(database_path=Path('unused'))
    panel = _DetailPanel(service,settings)
    panel._person = _person('MBOUNGOU MAKITA JEAN PAUL')
    saved = []
    panel.name_format_saved.connect(lambda:saved.append(True))
    panel._edit_name_format()
    service.save_name_format.assert_called_once_with(cfg,panel._person,'MBOUNGOU MAKITA','Jean Paul',settings)
    assert saved == [True]
    panel.close()
    app.removeTranslator(translator)
