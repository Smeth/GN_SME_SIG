"""Public write preconditions and the congés form's displayed version."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest


@pytest.mark.parametrize('family',['mission','stage','desertion','conge','malade','diplome','passation','affectation','distinction'])
@pytest.mark.parametrize('verb',['update','delete'])
def test_history_missing_snapshot_is_refused_before_opening_transaction(monkeypatch,family,verb):
    from sme_gend.services.dossier_write import atomic_dossier_write
    import sme_gend.services.personnel_service as service
    def forbidden(*args,**kwargs):pytest.fail('Missing version entered a transaction')
    monkeypatch.setattr(service,'_personnel_write',forbidden)
    def operation(self,identifier):pytest.fail('Unversioned write reached the repository')
    operation.__name__=verb+'_'+family
    owner=SimpleNamespace(_settings=object(),_assert_history_mutation=lambda action:None)
    with pytest.raises(ValueError,match='Version'):atomic_dossier_write(operation)(owner,1)


@pytest.mark.parametrize('conflict',[False,True])
def test_conges_form_sends_displayed_history_without_refreshing_after_conflict(monkeypatch,conflict):
    from PySide6.QtWidgets import QApplication
    from sme_gend.ui.views import conges_view as view
    app=QApplication.instance() or QApplication([])
    shown=SimpleNamespace(id=23);movement=SimpleNamespace(conge=shown,liste=SimpleNamespace(id='42',noms='SYNTHETIC',satellite_join_key=lambda:'42'))
    owner=SimpleNamespace(_check_can_edit=lambda:True,_selected_movement=lambda:movement,
        _conge_dates=lambda:(date(2026,10,1),date(2026,10,4)),_f_type=SimpleNamespace(currentData=lambda:'CA'),
        _f_obs=SimpleNamespace(text=lambda:'synthetic'),_view_mois='2026-10',_scoped_settings=object(),
        _view_data=MagicMock(),_reload_lists_async=MagicMock())
    service=MagicMock()
    if conflict:service.update_conge.side_effect=ValueError('synthetic conflict')
    monkeypatch.setattr('sme_gend.services.agent_dossier_events_service.AgentDossierEventsService',lambda settings:service)
    monkeypatch.setattr(view,'confirm_yes_no',lambda *args,**kwargs:True)
    monkeypatch.setattr(view.QMessageBox,'critical',MagicMock());monkeypatch.setattr(view.QMessageBox,'information',MagicMock())
    view.CongesView._save_conge_fiche(owner)
    assert service.update_conge.call_args.kwargs['expected'] is shown
    if conflict:
        owner._view_data.mark_stale.assert_not_called();owner._reload_lists_async.assert_not_called()
    else:owner._reload_lists_async.assert_called_once()
