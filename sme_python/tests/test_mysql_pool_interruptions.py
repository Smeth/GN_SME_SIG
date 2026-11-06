"""Pool cleanup must survive interruptions without committing a later operation."""
from pathlib import Path
import threading
from unittest.mock import MagicMock

import pytest

from sme_gend.config.settings import Settings
from sme_gend.repositories import mysql_pool as pools


@pytest.fixture(autouse=True)
def synthetic_maintenance_barrier(monkeypatch):
    # Pool doubles have no SQL engine; barrier semantics have native coverage.
    from contextlib import nullcontext
    monkeypatch.setattr('sme_gend.services.database_maintenance.coordinated_write', lambda *a, **kw: nullcontext())


@pytest.fixture
def isolated_pool(monkeypatch):
    monkeypatch.setattr(pools, '_thread_local', threading.local())
    monkeypatch.setattr(pools, '_bg_pools', {})
    settings = Settings(database_path=Path('unused.mdb'))
    first, second = MagicMock(), MagicMock()
    factory = MagicMock(side_effect=[first, second])
    monkeypatch.setattr(pools, '_new_connection', factory)
    yield settings, first, second, factory
    pools.invalidate_mysql_pool()


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('background', [False, True])
def test_interrupted_operation_rolls_back_and_remains_interrupted(isolated_pool, monkeypatch, interruption, background):
    settings, first, _, _ = isolated_pool
    if background:
        monkeypatch.setattr(pools, '_MAIN_THREAD', object())
    original = interruption('synthetic interrupted operation')
    with pytest.raises(interruption) as raised:
        with pools.pooled_connection(settings):
            raise original
    assert raised.value is original
    first.commit.assert_not_called()
    first.rollback.assert_called_once()
    if background:
        first.close.assert_called_once()
        pool = pools._get_bg_pool(settings)
        assert pool._created == 0 and pool._idle.empty()


def test_failed_rollback_retires_the_main_connection_before_next_operation(isolated_pool):
    settings, first, second, factory = isolated_pool
    first.rollback.side_effect = OSError('synthetic failed rollback')
    original = ValueError('synthetic rejected business write')
    with pytest.raises(ValueError) as raised:
        with pools.pooled_connection(settings):
            raise original
    assert raised.value is original
    first.close.assert_called_once()
    assert pools._pool_key(settings) not in pools._thread_local.conns
    with pools.pooled_connection(settings) as connection:
        assert connection is second
    assert factory.call_count == 2
    first.commit.assert_not_called()
    second.commit.assert_called_once()


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interrupted_connection_creation_returns_the_reserved_slot(monkeypatch, interruption):
    settings = Settings(database_path=Path('unused.mdb'))
    original = interruption('synthetic connection creation interruption')
    factory = MagicMock(side_effect=original)
    monkeypatch.setattr(pools, '_new_connection', factory)
    pool = pools._BoundedPool(settings, max_size=1)
    with pytest.raises(interruption) as raised:
        pool.acquire()
    assert raised.value is original and pool._created == 0
    factory.side_effect = None
    factory.return_value = MagicMock()
    borrowed = pool.acquire()
    pool.release(borrowed)
    pool.close_all()


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interrupted_readonly_initialization_closes_the_new_connection(monkeypatch, interruption):
    connection = MagicMock()
    original = interruption('synthetic read-only setup interruption')
    connection.cursor.return_value.__enter__.return_value.execute.side_effect = original
    monkeypatch.setattr(pools.pymysql, 'connect', lambda **kwargs: connection)
    with pytest.raises(interruption) as raised:
        pools._new_connection(Settings(database_path=Path('unused.mdb')), read_only=True)
    assert raised.value is original
    connection.close.assert_called_once()



@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('background', [False, True])
def test_interrupted_readonly_operation_retires_its_connection(isolated_pool, monkeypatch, interruption, background):
    settings, first, _, _ = isolated_pool
    if background:
        monkeypatch.setattr(pools, '_MAIN_THREAD', object())
    original = interruption('synthetic interrupted read')
    with pytest.raises(interruption) as raised:
        with pools.pooled_connection(settings, read_only=True):
            raise original
    assert raised.value is original
    first.commit.assert_not_called()
    first.close.assert_called_once()
    if background:
        pool = pools._get_bg_pool(settings, read_only=True)
        assert pool._created == 0 and pool._idle.empty()
    else:
        assert pools._pool_key(settings, read_only=True) not in pools._thread_local.conns


@pytest.mark.parametrize('cleanup_error', [OSError, KeyboardInterrupt])
@pytest.mark.parametrize('background', [False, True])
def test_failed_cleanup_never_masks_the_business_error(isolated_pool, monkeypatch, caplog, cleanup_error, background):
    settings, first, _, _ = isolated_pool
    if background:
        monkeypatch.setattr(pools, '_MAIN_THREAD', object())
    first.rollback.side_effect = cleanup_error('SYNTHETIC PRIVATE DETAIL')
    first.close.side_effect = cleanup_error('SYNTHETIC PRIVATE DETAIL')
    original = ValueError('synthetic rejected operation')
    with pytest.raises(ValueError) as raised:
        with pools.pooled_connection(settings):
            raise original
    assert raised.value is original
    first.close.assert_called_once()
    first.commit.assert_not_called()
    assert 'SYNTHETIC PRIVATE DETAIL' not in caplog.text
    assert 'connexion retirée' in caplog.text and 'connexion exclue' in caplog.text
    if background:
        assert pools._get_bg_pool(settings)._created == 0
    else:
        assert pools._pool_key(settings) not in pools._thread_local.conns


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('after_wait', [False, True])
def test_interrupted_background_probe_does_not_lose_a_borrowed_slot(isolated_pool, monkeypatch, interruption, after_wait):
    from concurrent.futures import ThreadPoolExecutor
    settings, first, _, _ = isolated_pool
    pool = pools._BoundedPool(settings, max_size=1)
    borrowed = pool.acquire()
    original = interruption('synthetic interrupted health probe')
    def interrupted(connection):
        assert connection is first
        raise original
    monkeypatch.setattr(pool, '_is_alive', interrupted)
    if after_wait:
        waiting = threading.Event()
        real_get = pool._idle.get
        def observe_wait(*args, **kwargs):
            if kwargs.get('block', True):
                waiting.set()
            return real_get(*args, **kwargs)
        monkeypatch.setattr(pool._idle, 'get', observe_wait)
        with ThreadPoolExecutor(max_workers=1) as workers:
            pending = workers.submit(pool.acquire)
            try:
                assert waiting.wait(3)
            finally:
                pool.release(borrowed)
            with pytest.raises(interruption) as raised:
                pending.result(timeout=3)
    else:
        pool.release(borrowed)
        with pytest.raises(interruption) as raised:
            pool.acquire()
    assert raised.value is original
    assert pool._created == 0 and pool._idle.empty()
    first.close.assert_called_once()
    pool.close_all()


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_interrupted_main_probe_discards_the_cached_connection(isolated_pool, monkeypatch, interruption):
    settings, first, _, _ = isolated_pool
    assert pools._main_thread_connection(settings) is first
    key = pools._pool_key(settings)
    pools._thread_local.checked_at[key] = 0.0
    original = interruption('synthetic interrupted health probe')
    first.ping.side_effect = original
    with pytest.raises(interruption) as raised:
        pools._main_thread_connection(settings)
    assert raised.value is original and key not in pools._thread_local.conns
    assert key not in pools._thread_local.checked_at
    first.close.assert_called_once()


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
@pytest.mark.parametrize('background', [False, True])
def test_interrupted_commit_is_propagated_without_retrying_the_operation(isolated_pool, monkeypatch, interruption, background):
    settings, first, _, factory = isolated_pool
    if background:
        monkeypatch.setattr(pools, '_MAIN_THREAD', object())
    original = interruption('synthetic interrupted commit acknowledgement')
    first.commit.side_effect = original
    writes = []
    with pytest.raises(interruption) as raised:
        with pools.pooled_connection(settings):
            writes.append('once')
    assert raised.value is original and writes == ['once']
    assert factory.call_count == 1
    first.commit.assert_called_once()
    first.rollback.assert_called_once()
    if background:
        first.close.assert_called_once()



@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_normal_retirement_does_not_swallow_an_interruption_during_close(isolated_pool, interruption):
    settings, first, _, _ = isolated_pool
    pool = pools._BoundedPool(settings, max_size=1)
    borrowed = pool.acquire()
    pool.close_all()
    original = interruption('synthetic interruption during normal retirement')
    first.close.side_effect = original
    with pytest.raises(interruption) as raised:
        pool.release(borrowed)
    assert raised.value is original and pool._created == 0 and pool._idle.empty()
