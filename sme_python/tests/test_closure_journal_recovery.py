"""Pure validation and synthetic local files; MySQL cases use isolated fixtures."""
import json
from pathlib import Path
import pytest
from sme_gend.services.sme_month_closure import _validated_closure_months
from sme_gend.services.local_audit_mirror import append_json_mirror
from sme_gend.services import audit_mirror_recovery as recovery


@pytest.mark.parametrize('source,target',[
    ('2026-00','2026-01'),('2026-13','2027-01'),('0000-01','2026-01'),
    ('2026-09','2026-09'),('2026-09','2026-08'),('missing','2026-10'),('2026-09','2026-13')])
def test_invalid_closure_dates_are_rejected(source,target):
    with pytest.raises(ValueError):_validated_closure_months(source,target)


def test_closure_accepts_existing_compact_month_formats_and_later_target():
    assert _validated_closure_months('092026','202611')==('2026-09','2026-11')
    assert _validated_closure_months('2026-09',None)==('2026-09',None)


@pytest.mark.parametrize('suffix',[b'{"partial":"\xc3',b'{"valid":2}'])
def test_a_partial_tail_is_preserved_and_a_complete_tail_is_not_lost(tmp_path,suffix):
    path=tmp_path/'audit.jsonl';path.write_bytes(b'{"original":1}\n'+suffix)
    append_json_mirror(path,[{'new':3}])
    entries=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert entries[0]=={'original':1} and entries[-1]=={'new':3}
    if b'partial' in suffix:
        fragments=list((tmp_path/'.audit-recovery').glob('*.part'))
        assert len(fragments)==1 and fragments[0].read_bytes()==suffix
    else:assert entries[1]=={'valid':2}


@pytest.mark.parametrize('route',[{'kind':'../outside'},{'kind':'personnel','month':'../2026-09'},
    {'kind':'personnel','month':'2026-13'},{'kind':'sensitive','path':'C:/outside'}])
def test_untrusted_delivery_cannot_select_an_arbitrary_file(route,monkeypatch,tmp_path):
    if route.get('kind')=='sensitive':
        monkeypatch.setattr('sme_gend.services.sensitive_audit_log._local_log_path',lambda:tmp_path/'allowed.jsonl')
        assert recovery.route_path(route)==tmp_path/'allowed.jsonl'
        assert recovery.route_for(tmp_path/'allowed.jsonl')=={'kind':'sensitive'}
        with pytest.raises(ValueError):recovery.route_for(tmp_path/'outside/allowed.jsonl')
    else:
        with pytest.raises(ValueError):recovery.route_path(route)


def test_a_new_profile_has_no_recovery_io_or_queue_query(monkeypatch,tmp_path):
    monkeypatch.setattr(recovery,'_node_file',lambda:tmp_path/'missing/node_id')
    monkeypatch.setattr(recovery,'MySQLConnection',lambda *a:pytest.fail('No SQL without an existing node'))
    assert recovery.recover_pending(object())=={'delivered':0,'failed':0}
    assert not (tmp_path/'missing').exists()



def test_interrupted_month_snapshot_never_publishes_a_partial_archive(tmp_path,monkeypatch):
    from sme_gend.services import sme_month_closure as closure
    source=tmp_path/'source.mdb';source.write_bytes(b'SOURCE')
    archives=tmp_path/'archives'
    def interrupt(source,target):
        Path(target).write_bytes(b'PARTIAL')
        raise OSError('synthetic snapshot interruption')
    monkeypatch.setattr(closure.shutil,'copy2',interrupt)
    with pytest.raises(OSError):closure._snapshot_file(source,archives,'sme_bundle_2026-09')
    assert list(archives.iterdir())==[] and source.read_bytes()==b'SOURCE'


def test_snapshot_collision_preserves_existing_archive_and_formats_stay_readable(tmp_path,monkeypatch):
    from sme_gend.services import sme_month_closure as closure
    source=tmp_path/'source.mdb';source.write_bytes(b'NEW')
    archives=tmp_path/'archives';archives.mkdir()
    previous=archives/'sme_bundle_2026-09_fixed.mdb';previous.write_bytes(b'OLD')
    monkeypatch.setattr(closure,'_archive_stamp',lambda:'fixed')
    with pytest.raises(FileExistsError):closure._snapshot_file(source,archives,'sme_bundle_2026-09')
    assert previous.read_bytes()==b'OLD'
    assert closure.extract_mois_from_archive_filename('sme_bundle_092026_20261030T120000Z.mdb')=='2026-09'
    assert closure.extract_mois_from_archive_filename('sme_bundle_092026_20261030T120000123456Z_abcdef123456.mdb')=='2026-09'



def test_automatic_recovery_respects_each_mirror_enable_flag(monkeypatch):
    monkeypatch.setenv('SME_AUDIT_JSON_MIRROR','0')
    monkeypatch.setenv('SME_SENSITIVE_AUDIT_JSON_MIRROR','0')
    assert recovery.enabled_mirror_kinds()==set()
    monkeypatch.setenv('SME_SENSITIVE_AUDIT_JSON_MIRROR','1')
    assert recovery.enabled_mirror_kinds()=={'sensitive'}
    monkeypatch.setenv('SME_AUDIT_JSON_MIRROR','1')
    assert recovery.enabled_mirror_kinds()=={'sensitive','personnel','activity','documents'}
