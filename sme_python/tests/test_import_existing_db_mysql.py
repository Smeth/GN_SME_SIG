"""Import archive mensuel : copie disque + chargement MySQL."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings


@pytest.fixture(autouse=True)
def legacy_archive_cycle(monkeypatch):
    monkeypatch.setattr("sme_gend.services.document_month_cycle.get_cycle_mode", lambda settings: "legacy")
    monkeypatch.setattr("sme_gend.services.document_month_cycle._has_dated_movements", lambda settings: False)


def test_import_existing_db_for_month_calls_mysql_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sme_gend.services import sme_month_closure as smc
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:None)

    source = tmp_path / "mai.mdb"
    source.write_bytes(b"fake")

    arch = tmp_path / "arch"
    arch.mkdir()
    monkeypatch.setattr(smc, "sme_archives_dir", lambda: arch)
    monkeypatch.setattr(
        smc,
        "_commit_closure_transition",
        lambda *a, **k: ([], 1, 1, False),
    )
    monkeypatch.setattr(smc, "append_audit_event", lambda **k: None)

    ctx = MagicMock()
    ctx.mois_fermes = []
    monkeypatch.setattr(
        "sme_gend.config.sme_context.load_sme_context", lambda: ctx
    )
    monkeypatch.setattr("sme_gend.config.sme_context.save_sme_context", lambda c: None)

    class _Ws:
        def close_month_transition(self, a, b):
            return "2026-06"

    monkeypatch.setattr(
        "sme_gend.services.sme_workspace_service.SmeWorkspaceService",
        lambda s: _Ws(),
    )

    bundle_called: list[str] = []

    def _fake_bundle(access_st, mysql_st, *, mois=None, progress=None, on_month_committed=None):
        bundle_called.append(str(mois))
        return {
            "sme_mois": mois,
            "liste_inserted": 10,
            "liste_updated": 2,
            "referentials": {"warnings": []},
        }

    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.import_full_mysql_bundle_from_access",
        _fake_bundle,
    )

    settings = Settings(database_path=source)
    result = smc.import_existing_db_for_month(
        settings,
        source,
        "2026-05",
        "2026-06",
    )

    assert bundle_called == ["2026-05"]
    assert result.mysql_bundle_result is not None
    assert result.mysql_bundle_result["liste_inserted"] == 10
    assert result.data_archive is not None
    assert result.data_archive.name.startswith("sme_bundle_")


def test_import_existing_db_for_month_archive_and_close_false_skips_archiving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``archive_and_close=False`` : import MySQL seul, aucune copie ni clôture TabSme."""
    from sme_gend.services import sme_month_closure as smc
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:None)

    source = tmp_path / "mai.mdb"
    source.write_bytes(b"fake")

    arch = tmp_path / "arch"
    arch.mkdir()
    monkeypatch.setattr(smc, "sme_archives_dir", lambda: arch)

    def _fail_closure(*a, **k):
        raise AssertionError("_apply_tabsme_closure ne doit pas être appelée")

    monkeypatch.setattr(smc, "_commit_closure_transition", _fail_closure)
    monkeypatch.setattr(smc, "append_audit_event", lambda **k: None)

    save_calls: list[object] = []
    monkeypatch.setattr(
        "sme_gend.config.sme_context.save_sme_context",
        lambda c: save_calls.append(c),
    )

    def _fake_bundle(access_st, mysql_st, *, mois=None, progress=None, on_month_committed=None):
        return {
            "sme_mois": mois,
            "liste_inserted": 5,
            "liste_updated": 1,
            "referentials": {"warnings": []},
        }

    monkeypatch.setattr(
        "sme_gend.services.mysql_bundle_import.import_full_mysql_bundle_from_access",
        _fake_bundle,
    )

    settings = Settings(database_path=source)
    result = smc.import_existing_db_for_month(
        settings,
        source,
        "2026-05",
        None,
        archive_and_close=False,
    )

    assert result.mysql_bundle_result is not None
    assert result.mysql_bundle_result["liste_inserted"] == 5
    assert result.data_archive is None
    assert result.tabsme_rows_closed == 0
    assert result.tabsme_rows_opened == 0
    assert result.tabsme_inserted_suivant is False
    assert list(arch.iterdir()) == []
    assert save_calls == []


def test_archive_without_activation_keeps_current_month(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sme_gend.services import sme_month_closure as smc
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:None)

    source = tmp_path / "mars.mdb"
    source.write_bytes(b"fake")
    archives = tmp_path / "archives"
    archives.mkdir()
    ctx = SimpleNamespace(mois_actif="2026-09", mois_fermes=[])
    monkeypatch.setattr(smc, "sme_archives_dir", lambda: archives)
    monkeypatch.setattr(smc, "_commit_closure_transition", lambda *a: ([], 1, 0, False))
    monkeypatch.setattr(smc, "append_audit_event", lambda **kw: None)
    monkeypatch.setattr("sme_gend.config.sme_context.load_sme_context", lambda: ctx)
    monkeypatch.setattr("sme_gend.config.sme_context.save_sme_context", lambda c: None)
    transitions = []
    def close(settings, source_month, next_month, **kwargs):
        transitions.append((source_month, next_month))
        return [], 1, 0, False
    monkeypatch.setattr(smc, "_commit_closure_transition", close)
    saved = []
    monkeypatch.setattr("sme_gend.config.sme_context.save_sme_context", lambda c: saved.append(c))
    smc.import_existing_db_for_month(Settings(database_path=source), source, "2026-03", None,
                                     import_to_mysql=False, archive_and_close=True)
    assert ctx.mois_actif == "2026-09"
    assert transitions == [("2026-03", None)]
    assert saved == [], "An old local context must not promote/reopen the imported month"



@pytest.mark.parametrize("next_month", ["2026-03", "2026-02"])
def test_import_rejects_invalid_activation_before_work(monkeypatch, next_month):
    from sme_gend.services import sme_month_closure as smc
    monkeypatch.setattr("sme_gend.services.active_month_guard.load_active_month_snapshot",lambda *a,**kw:None)
    with pytest.raises(ValueError, match="postérieur"):
        smc.import_existing_db_for_month(Settings(database_path=Path("missing.mdb")),
                                         Path("missing.mdb"), "2026-03", next_month)
