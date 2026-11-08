"""Réimport : remplacement strictement limité au mois choisi."""
from datetime import date
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sme_gend.services import mysql_bundle_import as bundle
from sme_gend.services import mysql_import_period as periods



def test_access_movement_import_skips_rows_from_other_months(monkeypatch):
    from sme_gend.services import mysql_access_numerique_import as importer
    cursor=MagicMock()
    monkeypatch.setattr(importer,"_agent_id",lambda cur,legacy_id:7)
    monkeypatch.setattr(importer,"_clamp_mouvement_payload",lambda **kw:(
        kw["leg"],kw["grade"],kw["nom"],kw["objet"],kw["objet"],kw["observ"],kw["observ"],kw["cdsme"]
    ))
    source_rows=[
        ("legacy-1","GND","AGENT","GAIN","082026","AJOUT"),
        ("legacy-2","GND","AGENT","PERTE","092026","RETRAITE"),
    ]
    diagnostics={}
    imported=importer.import_mouvements_from_access(
        cursor,lambda _sql:(source_rows,None),None,sme_periode_id=99,
        import_lot_id=123,mois_import="2026-09",replace_all=True,
        diagnostics=diagnostics,
    )
    assert imported==1 and diagnostics["other_month"]==1
    deletes=[call for call in cursor.execute.call_args_list if "DELETE FROM mouvement_effectif" in call.args[0]]
    inserts=[call for call in cursor.execute.call_args_list if "INSERT INTO mouvement_effectif" in call.args[0]]
    assert len(deletes)==1 and deletes[0].args[1]==(99,)
    assert len(inserts)==1 and inserts[0].args[1][0:3]==(99,7,"legacy-2")
    assert inserts[0].args[1][8]=="092026"

def test_access_movement_import_aborts_on_unknown_month_before_replacement(monkeypatch):
    from sme_gend.services import mysql_access_numerique_import as importer
    cursor=MagicMock()
    source_rows=[("legacy-1","GND","AGENT","GAIN",None,"AJOUT")]
    with pytest.raises(ValueError,match="sans Cdsme"):
        importer.import_mouvements_from_access(
            cursor,lambda _sql:(source_rows,None),None,sme_periode_id=99,
            import_lot_id=123,mois_import="2026-09",replace_all=True,
        )
    assert not any("DELETE FROM mouvement_effectif" in str(call.args[0]) for call in cursor.execute.call_args_list)


def test_purge_month_covers_dossier_history_without_deleting_agents(monkeypatch):
    monkeypatch.setattr('sme_gend.services.automatic_position_links.external_position_links', lambda *a: [])
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (51,)
    cursor.fetchall.return_value = ((51,),)
    cursor.rowcount = 2
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    factory = MagicMock()
    factory.connect.return_value = connection
    monkeypatch.setattr(periods, "MySQLConnection", lambda settings: factory)
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.invalidate_liste_cache", lambda s: None)
    result = periods.purge_sme_periode_for_reimport(object(), "202604", imported_only=False)
    statements = [(call.args[0], call.args[1] if len(call.args)>1 else ()) for call in cursor.execute.call_args_list]
    deleted = {table for table in (
        "agent_mission", "agent_malade", "agent_desertion", "parcours_stage",
        "mouvement_effectif", "agent_conge", "carriere_situation",
        "agent_diplome", "agent_distinction", "agent_passation_grade",
        "agent_affectation", "agent_position_event",
    ) if result.get(table) == 2}
    assert len(deleted) == 12
    assert result["sme_mois"] == "2026-04"
    assert result["agent_orphans_removed"] == 0
    for sql, params in statements:
        if not sql.lstrip().startswith("DELETE"):
            continue
        assert "WHERE" in sql
        assert sql.strip() != "DELETE FROM agent"
        assert "DELETE FROM agent_effectif_movement" not in sql
        assert 51 in params or "202604" in params or "2026-04" in params or "Import archive 202604" in params
        if "date_obtention" in sql or "date_attribution" in sql or "date_passation" in sql:
            assert date(2026, 4, 1) in params
            assert date(2026, 5, 1) in params
            assert "NOT LIKE 'Import archive %%'" in sql
        if "LEFT JOIN import_lot" in sql:
            assert "l.sme_periode_id = %s" in sql


def test_purge_missing_month_does_not_delete_anything(monkeypatch):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    factory = MagicMock()
    factory.connect.return_value = connection
    monkeypatch.setattr(periods, "MySQLConnection", lambda s: factory)
    result = periods.purge_sme_periode_for_reimport(object(), "2026-04")
    assert result["skipped"] == 1
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize("source_error", [False, True])
def test_source_read_must_succeed_before_purge(monkeypatch, source_error):
    calls = []
    def read():
        calls.append("read")
        if source_error:
            raise RuntimeError("Fichier Access illisible")
        return []
    source = MagicMock()
    source.fetch_all.side_effect = read
    monkeypatch.setattr("sme_gend.repositories.liste_generale_access_repository.AccessListeGeneraleRepository", lambda s: source)
    def purge(settings, mois):
        calls.append("purge")
        assert mois == "2026-04"
        return {"carriere_situation": 2}
    monkeypatch.setattr(periods, "purge_sme_periode_for_reimport", purge)
    if source_error:
        with pytest.raises(RuntimeError):
            bundle.replace_import_month_data(object(), object(), "2026-04")
        assert calls == ["read"]
    else:
        assert bundle.replace_import_month_data(object(), object(), "2026-04") == {"carriere_situation": 2}
        assert calls == ["read", "purge"]


def test_bundle_runs_replacement_before_loading_new_rows(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("sme_gend.services.active_month_guard.require_period_scope",lambda *a:SimpleNamespace(before=SimpleNamespace(active_month="2026-05")))
    monkeypatch.setattr("sme_gend.services.import_stability.ensure_import_schema", lambda s: None)
    monkeypatch.setattr("sme_gend.config.sme_context.load_sme_context", lambda: MagicMock(mois_actif="2026-05"))
    monkeypatch.setattr(bundle, "prepare_import_context", lambda *a: (51, 52, "2026-04", True))
    replacement = MagicMock(side_effect=RuntimeError("Stop après appel du remplacement"))
    monkeypatch.setattr(bundle, "replace_import_month_data", replacement)
    load = MagicMock()
    monkeypatch.setattr(bundle, "copy_referentials_and_satellites_to_mysql", load)
    access, settings = object(), object()
    with pytest.raises(RuntimeError, match="Stop après"):
        bundle._run_mysql_bundle_import(access, settings, original_access_path=Path("archive.mdb"),
                                       progress=None, mois="2026-04")
    replacement.assert_called_once_with(access, settings, "2026-04")
    load.assert_not_called()


def test_import_report_discloses_access_movements_ignored_from_other_months():
    from sme_gend.services.import_report import format_import_result_html
    message=format_import_result_html({
        "sme_mois":"2026-09",
        "referentials":{"warnings":["Gains & Pertes : 3 ligne(s) Access ignorée(s), car leur Cdsme appartient à un autre mois que 2026-09."]},
    },include_alerts=False)
    assert "3 ligne(s) Access ignorée(s)" in message
    assert "2026-09" in message


def test_result_reports_replacement_counts():
    from sme_gend.services.import_report import format_import_result_html
    report = format_import_result_html({"sme_mois": "2026-04", "month_replacement": {
        "sme_mois": "2026-04", "sme_periode_id": 51,
        "carriere_situation": 2, "agent_diplome": 3,
    }}, include_alerts=False)
    assert "Mois remplacé" in report
    assert "5 ancienne(s)" in report


@pytest.mark.parametrize("failure", [False, True])
def test_import_transaction_defers_nested_commits_and_rolls_back(monkeypatch, failure):
    from sme_gend.config.settings import Settings
    from sme_gend.repositories import mysql_connection as connections
    from sme_gend.repositories import mysql_common

    settings = Settings(database_path=Path("test.mdb"), mysql_database="test_replacement")
    database = ["ancienne carrière", "ancien congé"]
    opened = []
    connection = MagicMock()
    @contextmanager
    def pooled(st, *, read_only=False):
        opened.append(st)
        original = list(database)
        try:
            yield connection
        except Exception:
            database[:] = original
            connection.rollback()
            raise
        else:
            connection.commit()
    monkeypatch.setattr("sme_gend.repositories.mysql_pool.pooled_connection", pooled)
    # Toute réexécution de DDL dans l'import invaliderait l'atomicité.
    schema_lock = MagicMock(side_effect=AssertionError("DDL dans une transaction"))
    monkeypatch.setattr(connections, "mysql_initialization_lock", schema_lock)
    monkeypatch.setattr(mysql_common, "mysql_initialization_lock", schema_lock)

    def execute_import():
        with connections.mysql_import_transaction(settings):
            assert connections.has_import_transaction(settings)
            periods.ensure_mysql_migrations(settings)
            mysql_common.ensure_mysql_schema(settings)
            with connections.MySQLConnection(settings).connect() as nested:
                database.clear()
                nested.commit()  # Ancien commit de dépôt : doit être différé.
                connection.commit.assert_not_called()
            with connections.MySQLConnection(replace(settings, mysql_view_mois="2026-04")).connect(read_only=True) as reader:
                assert reader._connection is connection
                assert database == []
            database.append("nouvelle carrière")
            if failure:
                raise RuntimeError("Échec import des satellites")

    if failure:
        with pytest.raises(RuntimeError, match="satellites"):
            execute_import()
        assert database == ["ancienne carrière", "ancien congé"]
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
    else:
        execute_import()
        assert database == ["nouvelle carrière"]
        connection.commit.assert_called_once()
    assert len(opened) == 1
    assert not connections.has_import_transaction(settings)


def test_public_bundle_uses_fresh_backup_and_transaction(monkeypatch):
    from sme_gend.config.settings import Settings
    events = []
    settings = Settings(database_path=Path("missing-test-source.mdb"))
    backup = MagicMock(return_value=Path("backup.zip"))
    monkeypatch.setattr("sme_gend.services.backup_restore_service.ensure_recent_backup", backup)
    monkeypatch.setattr("sme_gend.repositories.mysql_common.ensure_mysql_schema", lambda s: events.append("schema"))
    monkeypatch.setattr("sme_gend.services.app_config_store.ensure_central_config_ready", lambda s: events.append("config"))
    monkeypatch.setattr("sme_gend.services.liste_generale_cache.invalidate_liste_cache", lambda s: None)
    monkeypatch.setattr("sme_gend.padmin_present.invalidate_padmin_presence_cache", lambda s: None)
    @contextmanager
    def transaction(s,**kwargs):
        events.append("begin")
        yield
        events.append("commit")
    monkeypatch.setattr("sme_gend.services.active_month_guard.period_state_write", transaction)
    def run(*a, **kw):
        events.append("import")
        return {"sme_mois": kw["mois"]}
    monkeypatch.setattr(bundle, "_run_mysql_bundle_import", run)
    assert bundle.import_full_mysql_bundle_from_access(settings, mois="2026-04") == {"sme_mois": "2026-04"}
    backup.assert_called_once_with(settings, max_age_days=0.0)
    assert events == ["schema", "config", "begin", "import", "commit"]
