from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path

from sme_gend.config.settings import Settings
from sme_gend.services import liste_generale_cache as cache


def test_edition_actualiser_invalidates_before_loading():
    from sme_gend.ui.views.edition_sme_view import EditionSmeView
    settings = Settings(database_path=Path('unused.mdb'))
    cache.get_liste_rows(settings, lambda: ['OLD'])
    def load(**kwargs):
        assert kwargs == {'preserve_ui': True}
        assert cache.get_liste_rows(settings, lambda: ['NEW']) == ['NEW']
    EditionSmeView._actualiser(SimpleNamespace(_settings=settings, load_data=load))


def test_personnel_actualiser_reads_persisted_rows(monkeypatch):
    from sme_gend.ui.views import personnel_list_view as module
    settings = Settings(database_path=Path('unused.mdb'))
    cache.invalidate_liste_cache(settings)
    cache.get_liste_rows(settings, lambda: ['OLD'])
    monkeypatch.setattr(module, 'run_with_progress', lambda parent, title, text, loader, **kw: loader())
    apply = Mock()
    service = SimpleNamespace(liste_complete=lambda: cache.get_liste_rows(settings, lambda: ['NEW']))
    view = SimpleNamespace(_settings=settings, _service=service, _apply_loaded_rows=apply, _refresh_lock_banner=Mock())
    module.PersonnelListView._refresh_all(view)
    apply.assert_called_once_with(['NEW'])


def test_dossier_reads_row_before_rendering_header():
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    calls = []
    view = SimpleNamespace(_constructing_prepared=False)
    for name in ('_reload_row_from_carriere', '_refresh_header_and_media', '_refresh_alerts_banner',
                 '_refresh_synthesis', '_refresh_dossier_tab', '_refresh_profil_tab', '_sync_history_edit_controls'):
        setattr(view, name, lambda key=name: calls.append(key))
    PersonnelDossierDialog._refresh_all(view)
    assert calls.index('_reload_row_from_carriere') < calls.index('_refresh_header_and_media')


def test_dossier_mutation_notifies_parent_list():
    from sme_gend.ui.personnel_dossier_dialog import PersonnelDossierDialog
    view = SimpleNamespace(_liste_generale_changed=False)
    for name in ('_reload_row_from_service', '_refresh_header_and_media', '_refresh_dossier_tab', '_refresh_synthesis'):
        setattr(view, name, Mock())
    PersonnelDossierDialog._after_carriere_dossier_mutation(view)
    assert view._liste_generale_changed
