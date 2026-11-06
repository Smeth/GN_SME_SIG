from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import MagicMock

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services import agent_dossier_sync as sync


def row(**kwargs):
    return ListeGeneraleRow(**({f.name: None for f in fields(ListeGeneraleRow)} |
                               {"id": "1", "noms": "SYNTHETIC", "structure": "U1"} | kwargs))


def test_historical_edit_uses_selected_month(monkeypatch):
    repo = MagicMock(spec=sync.MySQLAgentDossierRepository)
    repo.resolve_agent_id.return_value = 7
    monkeypatch.setattr(sync, "MySQLAgentDossierRepository", lambda st: repo)
    monkeypatch.setattr(sync, "_active_mois", lambda: "2026-09")
    before = row()
    sync.sync_dossier_after_personnel_save(Settings(database_path=Path("unused"), mysql_view_mois="2026-04"),
                                          before, replace(before, structure="U2"))
    assert repo.record_list_saisie_affectation.call_args.kwargs["sme_periode_mois"] == "2026-04"
