"""Scoped presentation saves and non-destructive grade reconciliation."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from sme_gend.config.settings import Settings
from sme_gend.models.limite_age_config import LimiteAgeConfig, GradeLimiteEntry
from sme_gend.services.limite_age_service import LimiteAgeService


@pytest.mark.parametrize('section', ['options','name','structures'])
def test_section_save_requires_a_loaded_version_before_sql(monkeypatch, section):
    service, cfg = LimiteAgeService(), LimiteAgeConfig()
    settings = Settings(database_path=Path('unused'))
    monkeypatch.setattr('sme_gend.services.auth_guard.session_has_permission', lambda _: True)
    monkeypatch.setattr('sme_gend.services.shared_reference_write.shared_reference_write',
                        lambda _: pytest.fail('An unbound editor must not open SQL'))
    person = SimpleNamespace(noms='SYN PERSON', satellite_join_key=lambda:'SYN-1')
    with pytest.raises(ValueError, match='Version de configuration indisponible'):
        if section == 'options': service.save_doc_options(cfg, settings)
        elif section == 'name': service.save_name_format(cfg, person, 'SYN','Person',settings)
        else: service.save_structure_format(cfg,settings)


@pytest.mark.parametrize('rows', [
    [GradeLimiteEntry('LTN',60,42),GradeLimiteEntry('Lieutenant',60,42)],
    [GradeLimiteEntry('',0,0)],
    [GradeLimiteEntry('MDC',55,37),GradeLimiteEntry('SCH',55,37),GradeLimiteEntry('ADM',58,40),GradeLimiteEntry('MAJ',58,40)],
])
def test_sync_only_coalesces_identical_aliases_and_keeps_distinct_codes(monkeypatch, rows):
    cfg = LimiteAgeConfig(grades=deepcopy(rows)); original=deepcopy(cfg.to_dict())
    service=LimiteAgeService();service.load=MagicMock(return_value=cfg);service.save_grades=MagicMock()
    monkeypatch.setattr('sme_gend.repositories.grade_repository.GradeRepository',
                        lambda _:SimpleNamespace(fetch_all=lambda:[]))
    result, added = service.sync_grades_from_referentiel(object())
    assert added == [] and cfg.to_dict() == original
    if rows[0].grade == 'LTN':
        assert [r.grade for r in result.grades] == ['LTN']
        assert service.find_grade_entry('Lieutenant', result) is result.grades[0]
        assert service.save_grades.call_count == 1
    elif rows[0].grade == '':
        assert result.grades == [] and service.save_grades.call_count == 1
    else:
        assert [r.grade for r in result.grades] == ['MDC','SCH','ADM','MAJ']
        service.save_grades.assert_not_called()


@pytest.mark.parametrize('rows, message', [
    ([GradeLimiteEntry('Grade spécial',55,35),GradeLimiteEntry('grade special',60,40)], 'Limites contradictoires'),
    ([GradeLimiteEntry('',60,42)], 'renseignez le grade'),
])
def test_sync_does_not_discard_conflicting_or_unidentified_values(monkeypatch, rows, message):
    cfg=LimiteAgeConfig(grades=rows);before=deepcopy(cfg.to_dict())
    service=LimiteAgeService();service.load=MagicMock(return_value=cfg);service.save_grades=MagicMock()
    with pytest.raises(ValueError, match=message):service.sync_grades_from_referentiel(object())
    service.save_grades.assert_not_called()
    assert cfg.to_dict() == before


def test_sync_editor_shows_cleanup_even_without_new_grades(monkeypatch):
    from sme_gend.ui.views import limite_age_view as ui
    app=QApplication.instance() or QApplication([])
    cfg=LimiteAgeConfig(grades=[GradeLimiteEntry('LTN',60,42),GradeLimiteEntry('Lieutenant',60,42)])
    cleaned=deepcopy(cfg);cleaned.grades=cleaned.grades[:1];cleaned._sync_cleanups=('Alias regroupé.',)
    service=MagicMock();service.load.return_value=cfg;service.sync_grades_from_referentiel.return_value=(cleaned,[])
    info=MagicMock();monkeypatch.setattr(QMessageBox,'information',info)
    view=ui._ConfigLimitesTab(Settings(database_path=Path('unused')),service)
    view._sync_from_db()
    assert view._table.rowCount()==1 and not view._dirty
    assert 'Alias regroupé.' in info.call_args.args[-1]
    view.deleteLater();app.processEvents()


def test_sync_preserves_a_draft_when_reload_is_cancelled(monkeypatch):
    from sme_gend.ui.views import limite_age_view as ui
    app=QApplication.instance() or QApplication([])
    service=MagicMock();service.load.return_value=LimiteAgeConfig(grades=[GradeLimiteEntry('Custom',55,35)])
    view=ui._ConfigLimitesTab(Settings(database_path=Path('unused')),service)
    view._table.item(0,1).setText('56')
    monkeypatch.setattr('sme_gend.ui.confirm_dialogs.confirm_yes_no',lambda *_,**__:False)
    view._sync_from_db()
    service.sync_grades_from_referentiel.assert_not_called()
    assert view._table.item(0,1).text()=='56' and view._dirty
    view.deleteLater();app.processEvents()
