from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from io import BytesIO
from pathlib import Path
import threading
from types import SimpleNamespace

from openpyxl import Workbook, load_workbook

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow


def test_inflight_read_cannot_reinstall_invalidated_cache():
    from sme_gend.services import liste_generale_cache as cache
    started, release = threading.Event(), threading.Event()
    key = (None, "synthetic-race")
    def slow():
        started.set()
        assert release.wait(3)
        return ["old"]
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(cache.get_liste_rows_by_key, key, slow)
        assert started.wait(3)
        cache.invalidate_liste_cache()
        release.set()
        pending.result()
    assert cache.get_liste_rows_by_key(key, lambda: ["fresh"]) == ["fresh"]
    cache.invalidate_liste_cache()


def test_rotating_credentials_replaces_background_pool(monkeypatch):
    from sme_gend.repositories import mysql_pool as pools
    monkeypatch.setattr(pools, "_bg_pools", {})
    first = Settings(database_path=Path("unused"), mysql_password="SYNTHETIC_A")
    before = pools._get_bg_pool(first)
    pools.invalidate_mysql_pool(first)
    after = pools._get_bg_pool(replace(first, mysql_password="SYNTHETIC_B"))
    assert after is not before
    assert after._settings.mysql_password == "SYNTHETIC_B"


def test_invalidated_pool_closes_borrowed_connection_on_return(monkeypatch):
    from unittest.mock import MagicMock
    from sme_gend.repositories import mysql_pool as pools
    conn = MagicMock()
    monkeypatch.setattr(pools, "_new_connection", lambda *args, **kwargs: conn)
    pool = pools._BoundedPool(Settings(database_path=Path("unused")), max_size=1)
    borrowed = pool.acquire()
    pool.close_all()
    pool.release(borrowed)
    conn.close.assert_called_once()
    assert pool._idle.empty()


def test_saturated_pool_has_bounded_wait(monkeypatch):
    import pytest
    from unittest.mock import MagicMock
    from sme_gend.repositories import mysql_pool as pools
    monkeypatch.setattr(pools, "_POOL_ACQUIRE_TIMEOUT", 0.01)
    monkeypatch.setattr(pools, "_new_connection", lambda *args, **kwargs: MagicMock())
    pool = pools._BoundedPool(Settings(database_path=Path("unused")), max_size=1)
    borrowed = pool.acquire()
    with pytest.raises(TimeoutError):
        pool.acquire()
    pool.release(borrowed)
    pool.close_all()


def test_text_starting_equals_roundtrips_as_text():
    from sme_gend.services.export_workbook_tables import table_sheet
    wb = Workbook()
    ws = table_sheet(wb, "test", "Title", ["Observation"], [["=1+1"]], mois="2026-09")
    output = BytesIO()
    wb.save(output)
    reread = load_workbook(output, data_only=False)
    assert reread["test"]["A4"].value == "=1+1"
    assert reread["test"]["A4"].data_type == "s"
    wb.close()
    reread.close()


def test_leave_details_choose_matching_type():
    from sme_gend.services.export_workbook_tables import detail_cells
    row = ListeGeneraleRow(**{f.name: None for f in fields(ListeGeneraleRow)} |
                           {"id": "7", "position_admin": "CongéAdmin"})
    admin = SimpleNamespace(type_conge="ADMIN", type_label="Administrative", date_debut="2026-09-01",
                            date_fin="2026-09-10", observation="")
    exp = SimpleNamespace(type_conge="EXPECTATIVE", type_label="Expectative", date_debut="2026-09-20",
                          date_fin="2026-09-30", observation="")
    details = {key: {} for key in ("missions", "malades", "deserteurs", "stages_loc", "stages_ex",
                                   "stages_internal", "conges", "conges_by_type", "positions")}
    details["conges"] = {"7": exp}
    details["conges_by_type"] = {("7", "ADMIN"): admin, ("7", "EXPECTATIVE"): exp}
    assert detail_cells(row, details)[3] == "2026-09-01"
    assert detail_cells(replace(row, position_admin="CongéExp"), details)[3] == "2026-09-20"
