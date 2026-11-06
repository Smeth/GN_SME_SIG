"""Tests réconciliation post-import bundle."""

from __future__ import annotations

from pathlib import Path

import pytest

from sme_gend.services.carriere_reconciliation import (
    CarriereReconciliationResult,
    CarriereUnresolvedCounts,
    reconcile_after_import,
    _log_unresolved_samples,
)


def test_reconcile_after_import_delegates_with_mois_and_lot(monkeypatch) -> None:
    captured: list[dict] = []

    def _fake(settings, *, mois, import_lot_id, log_unresolved=True):
        captured.append(
            {
                "mois": mois,
                "import_lot_id": import_lot_id,
                "log_unresolved": log_unresolved,
            }
        )
        return CarriereReconciliationResult(mois=mois)

    monkeypatch.setattr(
        "sme_gend.services.carriere_reconciliation.reconcile_carriere_referentiels",
        _fake,
    )

    result = reconcile_after_import(object(), mois="2026-03", import_lot_id=99)

    assert captured == [{"mois": "2026-03", "import_lot_id": 99, "log_unresolved": True}]
    assert result.mois == "2026-03"


def test_carriere_reconciliation_to_summary_dict() -> None:
    r = CarriereReconciliationResult(
        fk_rows_updated=10,
        ordre_tri_updated=4,
        unresolved_before=CarriereUnresolvedCounts(grade=2, structure=1),
        unresolved_after=CarriereUnresolvedCounts(grade=1),
        etl_errors_logged=3,
        mois="2026-01",
    )
    s = r.to_summary_dict()
    assert s["fk_updated"] == 10
    assert s["unresolved_before"] == 3
    assert s["unresolved_after"] == 1
    assert s["mois"] == "2026-01"


def test_log_unresolved_samples_all_fk_types(monkeypatch) -> None:
    logged: list[str] = []

    def _fake_log(cur, *, import_lot_id, table_cible, cle_legacy, message):
        logged.append(message)

    monkeypatch.setattr(
        "sme_gend.services.carriere_reconciliation.log_etl_erreur",
        _fake_log,
    )

    class _Cur:
        def __init__(self) -> None:
            self._calls = 0

        def execute(self, sql, params=()) -> None:
            self._calls += 1

        def fetchall(self):
            if self._calls <= 5:
                return [(1, "LEG-1", "valeur")]
            return []

    n = _log_unresolved_samples(_Cur(), import_lot_id=7, mois="2026-05", limit=200)

    assert n == 5
    assert any("Grade non résolu" in m for m in logged)
    assert any("Structure non résolue" in m for m in logged)
    assert any("Position admin non résolue" in m for m in logged)
    assert any("Fonction non résolue" in m for m in logged)
    assert any("Subdivision d'arme non résolue" in m for m in logged)


def test_bundle_import_runs_reconciliation_and_invalidates_cache(monkeypatch) -> None:
    from contextlib import nullcontext
    from types import SimpleNamespace
    state=SimpleNamespace(before=SimpleNamespace(active_month="2026-04"))
    monkeypatch.setattr("sme_gend.services.active_month_guard.period_state_write",lambda *a,**kw:nullcontext(state))
    monkeypatch.setattr("sme_gend.services.active_month_guard.require_period_scope",lambda *a:state)
    monkeypatch.setattr("sme_gend.services.shared_reference_write.mark_shared_data_write",lambda changed:None)
    monkeypatch.setattr("sme_gend.services.data_sync.publish_data_change", lambda *a, **kw: None)

    monkeypatch.setattr("sme_gend.repositories.mysql_common.ensure_mysql_schema", lambda s: None)
    monkeypatch.setattr("sme_gend.services.app_config_store.ensure_central_config_ready", lambda s: None)
    monkeypatch.setattr("sme_gend.repositories.mysql_connection.mysql_import_transaction", lambda s: nullcontext())
    monkeypatch.setattr("sme_gend.services.mysql_bundle_import.replace_import_month_data", lambda *a: {})
    reconcile_calls: list[tuple] = []
    cache_cleared: list[object] = []

    monkeypatch.setattr(
        "sme_gend.services.backup_restore_service.ensure_recent_backup",
        lambda *_a, **_k: object(),
    )
    monkeypatch.setattr(
        "sme_gend.services.import_stability.ensure_import_schema",
        lambda _s: None,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.prepare_import_context",
        lambda *_a, **_k: (1, 42, "2026-04", None),
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_import_period.is_historical_import",
        lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_import_period.normalize_import_mois",
        lambda m: m,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.copy_referentials_and_satellites_to_mysql",
        lambda *_a, **_k: {"counts": {}, "warnings": [], "ref_cache": None},
    )
    from sme_gend.services.liste_generale_import_stats import ListeGeneraleImportStats

    monkeypatch.setattr(
        "sme_gend.services.liste_generale_import.import_liste_generale_access_to_mysql",
        lambda *_a, **_k: (
            0,
            5,
            [],
            ListeGeneraleImportStats(rows_read=5, agents_distinct=5, updated=5),
        ),
    )

    class _Repo:
        def link_satellite_agent_ids(self) -> None:
            pass

    monkeypatch.setattr(
        "sme_gend.repositories.mysql_agent_repository.MySQLAgentRepository",
        lambda _s: _Repo(),
    )
    monkeypatch.setattr(
        "sme_gend.services.agent_dossier_import_enrichment.enrich_dossier_after_import",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "sme_gend.services.agent_conge_service.sync_conges_from_carriere_period",
        lambda *_a, **_k: 0,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.refresh_est_actuelle_flags",
        lambda *_a, **_k: 0,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.refresh_carriere_segment_dates",
        lambda *_a, **_k: 0,
    )
    monkeypatch.setattr(
        "sme_gend.services.sme_workspace_service.SmeWorkspaceService",
        lambda _s: type("Ws", (), {"get_active_mois": lambda self: "2026-04"})(),
    )
    monkeypatch.setattr(
        "sme_gend.services.audit_log.append_audit_event",
        lambda **_k: None,
    )
    monkeypatch.setattr(
        "sme_gend.config.sme_context.load_sme_context",
        lambda: type("Ctx", (), {"mois_actif": "2026-04"})(),
    )
    class _FakeMC:
        def __init__(self, _settings) -> None:
            pass

        def connect(self):
            return type(
                "Conn",
                (),
                {
                    "__enter__": lambda s: s,
                    "__exit__": lambda s, *a: None,
                    "cursor": lambda s: type(
                        "Cur",
                        (),
                        {
                            "__enter__": lambda c: c,
                            "__exit__": lambda c, *a: None,
                        },
                    )(),
                },
            )()

    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.MySQLConnection",
        _FakeMC,
    )

    def _reconcile(settings, *, mois, import_lot_id):
        reconcile_calls.append((mois, import_lot_id))
        return CarriereReconciliationResult(
            fk_rows_updated=5,
            mois=mois,
            unresolved_after=CarriereUnresolvedCounts(),
        )

    monkeypatch.setattr(
        "sme_gend.services.carriere_reconciliation.reconcile_after_import",
        _reconcile,
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_import_period.repair_carriere_liste_duplicates",
        lambda _s: {},
    )
    monkeypatch.setattr(
        "sme_gend.services.sot_maintenance.project_carriere_from_dossier_batch",
        lambda *_a, **_k: {"projected": 0, "mois": "2026-04"},
    )
    monkeypatch.setattr(
        "sme_gend.services.liste_generale_cache.invalidate_liste_cache",
        lambda settings: cache_cleared.append(settings),
    )

    from pathlib import Path

    from sme_gend.config.settings import Settings

    access_st = Settings(database_path=Path("test.mdb"))
    mysql_st = Settings(
        database_path=Path("test.mdb"),
        mysql_database="smegend",
    )

    from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access

    result = import_full_mysql_bundle_from_access(access_st, mysql_st, mois="2026-04")

    assert reconcile_calls == [("2026-04", 42)]
    assert len(cache_cleared) == 1
    assert result["carriere_reconciliation"].fk_rows_updated == 5


def test_bundle_import_stops_when_safety_backup_fails(monkeypatch) -> None:
    from sme_gend.config.settings import Settings
    from sme_gend.services.mysql_bundle_import import import_full_mysql_bundle_from_access

    monkeypatch.setattr(
        "sme_gend.services.backup_restore_service.ensure_recent_backup",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import._run_mysql_bundle_import",
        lambda *_a, **_k: pytest.fail("L'import ne doit pas démarrer sans sauvegarde."),
    )
    settings = Settings(database_path=Path("source.mdb"), mysql_database="smegend")

    with pytest.raises(RuntimeError, match="sauvegarde de sécurité préalable"):
        import_full_mysql_bundle_from_access(settings, settings, mois="2026-04")


# Persistence is covered on native MySQL; this file exercises its fake repository.
import pytest

@pytest.fixture(autouse=True)
def isolated_audit_persistence(monkeypatch):
    monkeypatch.setattr('sme_gend.services.audit_log._append_audit_mysql',lambda *a,**kw:None)
