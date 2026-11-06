from __future__ import annotations

import sys
from unittest.mock import MagicMock
from contextlib import nullcontext
from pathlib import Path
from sme_gend.config.settings import Settings

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from sme_gend.models.gain_perte import GainPerteRow
from sme_gend.repositories.mysql_bundle_repositories import MySQLGainPerteRepository
from sme_gend.ui.view_lifecycle import ViewDataState


def _repo() -> MySQLGainPerteRepository:
    repo = MySQLGainPerteRepository.__new__(MySQLGainPerteRepository)
    repo._settings = Settings(database_path=Path("unused.mdb"))
    return repo


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def test_fetch_for_month_uses_sme_period_as_authoritative_month():
    repo=_repo();cursor=MagicMock();cursor.fetchall.return_value=[]
    conn=MagicMock();conn.cursor.return_value.__enter__.return_value=cursor
    repo._connect=lambda:_context(conn)
    repo.fetch_for_cdsme("092026")
    sql=" ".join(cursor.execute.call_args.args[0].split())
    assert "WHERE p.annee_mois = %s" in sql
    assert "OR (p.id IS NULL AND m.cdsme_derive = %s)" in sql


def test_fetch_all_propagates_database_errors():
    repo = _repo()
    repo._connect = MagicMock(side_effect=RuntimeError("database unavailable"))

    with pytest.raises(RuntimeError, match="database unavailable"):
        repo.fetch_all()


def test_update_row_relinks_movement_to_resolved_agent(monkeypatch):
    import sme_gend.services.mysql_access_numerique_import as movement_import
    import sme_gend.services.mysql_import_period as period_import

    from sme_gend.repositories import mysql_bundle_repositories as repositories
    monkeypatch.setattr(repositories, "shared_data_write", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(repositories, "mark_shared_data_write", lambda changed: None)
    monkeypatch.setattr(repositories, "require_movement_period_open", lambda _cur, _mid: "2026-04")
    monkeypatch.setattr(repositories, "require_period_open", lambda _cur, _ym: 18)
    repo = _repo()
    cursor = MagicMock()
    cursor.rowcount = 1
    cursor.fetchone.return_value = ("a" * 64,)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    repo._connect = lambda: _context(conn)
    repo._resolve_agent_and_periode = lambda _cur, _row: (73, 18)
    monkeypatch.setattr(
        movement_import,
        "_clamp_mouvement_payload",
        lambda **_kwargs: (
            "legacy-2", "GND", "Agent cible", "PERTE", "PERTE",
            "RETRAITE", "RETRAITE", "052026",
        ),
    )
    monkeypatch.setattr(period_import, "last_day_of_month", lambda _ym: "2026-05-31")
    row = GainPerteRow(
        id="legacy-2",
        grade="GND",
        nom="Agent cible",
        objet="PERTE",
        cdsme="052026",
        observ="RETRAITE",
        mois_ym="2026-05",
        movement_id=901,
        write_token=(901, "a" * 64),
    )

    repo.update_row(row, where_id="legacy-1", where_cdsme="042026", movement_id=901)

    sql, params = cursor.execute.call_args.args
    assert "m.agent_id=%s" in " ".join(sql.split())
    assert params[:3] == (18, 73, "legacy-2")
    assert params[-1] == 901


def test_gain_perte_load_error_is_visible_and_retryable(qapp, monkeypatch):
    from sme_gend.ui.views import gain_perte_view

    view = gain_perte_view.GainPerteView.__new__(gain_perte_view.GainPerteView)
    QWidget.__init__(view)
    view._view_data = ViewDataState(stale=True)
    view._reload_busy = False
    view._repo = MagicMock()
    view._repo.fetch_all.side_effect = RuntimeError("base indisponible")
    warnings = []

    def run_inline(_parent, work, _done, *, on_error=None):
        try:
            work()
        except Exception as exc:
            on_error(exc)

    monkeypatch.setattr(gain_perte_view, "run_in_background", run_inline)
    monkeypatch.setattr(
        gain_perte_view.QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )

    view._reload_async()

    assert not view._reload_busy
    assert len(warnings) == 1
    assert "base indisponible" in warnings[0][-1]
    assert view._view_data.begin_load()


def _context(value):
    class Context:
        def __enter__(self):
            return value

        def __exit__(self, *_args):
            return False

    return Context()


# Persistence is covered on native MySQL; this file exercises its fake repository.
import pytest

@pytest.fixture(autouse=True)
def isolated_audit_persistence(monkeypatch):
    monkeypatch.setattr('sme_gend.services.business_audit.record_business_action',lambda *a,**kw:None)
