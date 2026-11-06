"""Local contracts of the shared-media gate, without external IO."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from sme_gend.config.settings import Settings
from sme_gend.services import media_storage_coordination as coordination, shared_media


@pytest.mark.parametrize('timeout',[-1,31,False,1.5,None])
def test_gate_rejects_invalid_timeouts_before_connection(monkeypatch,timeout):
    monkeypatch.setattr(coordination.pymysql,'connect',lambda **kwargs:pytest.fail('Unexpected connection'))
    with pytest.raises(ValueError):
        with coordination.media_storage_gate(Settings(database_path=Path('unused')),timeout=timeout):
            pytest.fail('Must not enter')


def test_lock_identity_uses_actual_schema_name_and_stays_within_mysql_limit():
    assert coordination.media_lock_name('SYNTHETIC')==coordination.media_lock_name('synthetic')
    assert coordination.media_lock_name('synthetic')!=coordination.media_lock_name('different')
    assert len(coordination.media_lock_name('x'*64))<=64


@pytest.mark.parametrize('raw',[[],42,'[]','null'])
def test_invalid_shared_configuration_fails_closed(raw):
    with pytest.raises(ValueError):coordination.decode_payload(raw)


def test_local_media_does_not_require_mysql_coordination(tmp_path,monkeypatch):
    monkeypatch.setattr(coordination.pymysql,'connect',lambda **kwargs:pytest.fail('Local override must stay local'))
    with shared_media.shared_media_write_target(tmp_path) as target:
        assert target.root==tmp_path
        assert target.settings is None and not target.shared
        target.check()


@pytest.mark.parametrize('row',[(42,42),(41,42),(42,41),(None,42)])
def test_gate_detects_session_replacement_and_lost_ownership(row):
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
        def fetchone(self):return row
    gate=coordination.MediaStorageGate(SimpleNamespace(cursor=Cursor),'synthetic',42)
    if row==(42,42):gate.check()
    else:
        with pytest.raises(coordination.MediaStorageLockLostError):gate.check()



def test_migration_file_lock_does_not_block_other_business_audit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sme_gend.services.local_audit_mirror import append_json_mirror
    import json
    ready,release=Event(),Event()
    def migration():
        with shared_media._root_file_locks([tmp_path/'media']):
            ready.set();assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as workers:
        moving=workers.submit(migration)
        assert ready.wait(3)
        journal=workers.submit(append_json_mirror,tmp_path/'business.jsonl',[{'synthetic':'business continues'}])
        try:
            journal.result(timeout=1)
        finally:
            release.set();moving.result(timeout=3)
    assert json.loads((tmp_path/'business.jsonl').read_text(encoding='utf-8'))=={'synthetic':'business continues'}
