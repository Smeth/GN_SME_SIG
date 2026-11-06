"""Atomic publication against synthetic Windows readers and controlled errors."""
import ctypes
import os
from pathlib import Path
import threading
import time
import pytest
from sme_gend.services import safe_paths


def win_error(code):
    error=PermissionError('synthetic Windows file error')
    error.winerror=code
    return error


class ReaderWithoutDeleteSharing:
    def __init__(self,path):
        from ctypes import wintypes
        self.api=ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
            wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        self.api.CreateFileW.restype=wintypes.HANDLE
        self.api.CloseHandle.argtypes=[wintypes.HANDLE]
        self.api.CloseHandle.restype=wintypes.BOOL
        self.handle=self.api.CreateFileW(str(path),0x80000000,3,None,3,0x80,None)
        assert self.handle!=ctypes.c_void_p(-1).value
        self.failed=threading.Event();self.closed=threading.Event()
        def close_after_failure():
            self.failed.wait(3)
            time.sleep(.06)
            assert self.api.CloseHandle(self.handle)
            self.closed.set()
        self.thread=threading.Thread(target=close_after_failure)
        self.thread.start()
    def finish(self):
        self.failed.set();self.thread.join(4)
        assert self.closed.is_set()


@pytest.mark.skipif(os.name!='nt',reason='Native Windows sharing flags')
def test_real_photo_reader_released_after_contention_allows_publication(tmp_path,monkeypatch):
    from sme_gend.services import personnel_media
    monkeypatch.setattr(personnel_media,'personnel_media_root',lambda:tmp_path)
    target=tmp_path/'photos/42.jpg';target.parent.mkdir();target.write_bytes(b'PREVIOUS')
    source=tmp_path/'new.jpg';source.write_bytes(b'SYNTHETIC NEW PHOTO'*1000)
    holder=ReaderWithoutDeleteSharing(target)
    original=Path.replace;failures=[]
    def observe(path,dest):
        try:return original(path,dest)
        except OSError as error:
            if Path(dest)==target:
                failures.append(error.winerror)
                assert target.read_bytes()==b'PREVIOUS'
                holder.failed.set()
            raise
    monkeypatch.setattr(Path,'replace',observe)
    try:
        assert personnel_media.save_person_photo('42',source)==target
    finally:holder.finish()
    assert failures and set(failures)<={5,32,33}
    assert target.read_bytes()==source.read_bytes()
    assert not list(tmp_path.rglob('.sme-*.part'))


def test_transient_rename_does_not_require_repeating_the_copy(tmp_path,monkeypatch):
    from sme_gend.services import file_contention
    monkeypatch.setattr(file_contention,'_WINDOWS',True)
    target=tmp_path/'configuration.json';target.write_bytes(b'PREVIOUS')
    original=Path.replace;calls=[]
    def busy_once(path,dest):
        if Path(dest)==target:
            calls.append(path.read_bytes())
            if len(calls)==1:raise win_error(32)
        return original(path,dest)
    monkeypatch.setattr(Path,'replace',busy_once)
    safe_paths.atomic_write(target,b'NEW COMPLETE')
    assert calls==[b'NEW COMPLETE',b'NEW COMPLETE']
    assert target.read_bytes()==b'NEW COMPLETE'


def test_cleanup_failure_never_replaces_the_original_operation_error(tmp_path,monkeypatch):
    original=ValueError('synthetic producer failure')
    def denied(*args,**kwargs):raise win_error(5)
    monkeypatch.setattr(Path,'unlink',denied)
    with pytest.raises(ValueError) as raised:
        with safe_paths.atomic_destination(tmp_path/'target.json') as temporary:
            temporary.write_bytes(b'PREPARED');raise original
    assert raised.value is original


@pytest.mark.skipif(os.name!='nt',reason='Source consumed by a Windows rename')
def test_successful_rename_does_not_fail_on_an_unnecessary_cleanup(tmp_path,monkeypatch):
    original=Path.unlink
    def disconnected_after_rename(path,*args,**kwargs):
        if path.name.startswith('.sme-'):
            error=OSError('synthetic disconnected share after successful rename');error.winerror=64;raise error
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',disconnected_after_rename)
    safe_paths.atomic_write(tmp_path/'target.json',b'COMPLETE')
    assert (tmp_path/'target.json').read_bytes()==b'COMPLETE'



@pytest.fixture
def retry_clock(monkeypatch):
    from sme_gend.services import file_contention as retry
    now=[0.0];pauses=[]
    monkeypatch.setattr(retry,'_WINDOWS',True)
    monkeypatch.setattr(retry,'_now',lambda:now[0])
    def advance(seconds):
        pauses.append(seconds);now[0]+=seconds
    monkeypatch.setattr(retry,'_pause',advance)
    return now,pauses


@pytest.mark.parametrize('code',[5,32,33])
def test_transient_sharing_errors_reuse_the_prepared_file(tmp_path,monkeypatch,retry_clock,code):
    target=tmp_path/'photo.jpg';target.write_bytes(b'OLD')
    original=Path.replace;calls=[]
    def contention(path,dest):
        calls.append(path)
        if len(calls)==1:
            assert target.read_bytes()==b'OLD'
            raise win_error(code)
        return original(path,dest)
    monkeypatch.setattr(Path,'replace',contention)
    safe_paths.atomic_write(target,b'NEW')
    assert len(calls)==2 and calls[0]==calls[1]
    assert target.read_bytes()==b'NEW'
    assert len(retry_clock[1])==1


def test_permanent_denial_expires_without_touching_original(tmp_path,monkeypatch,retry_clock):
    target=tmp_path/'photo.jpg';target.write_bytes(b'OLD')
    original=win_error(5);calls=[]
    def denied(path,dest):
        calls.append(path);raise original
    monkeypatch.setattr(Path,'replace',denied)
    with pytest.raises(PermissionError) as raised:safe_paths.atomic_write(target,b'NEW')
    assert raised.value is original
    assert 1<len(calls)<=41 and .99<=retry_clock[0][0]<=1.01
    assert target.read_bytes()==b'OLD' and not list(tmp_path.glob('.sme-*'))


@pytest.mark.parametrize('code',[53,64,67,112,1231,None])
def test_other_io_errors_fail_without_retries(tmp_path,monkeypatch,retry_clock,code):
    target=tmp_path/'photo.jpg';target.write_bytes(b'OLD')
    original=win_error(code);calls=[]
    def failed(path,dest):calls.append(path);raise original
    monkeypatch.setattr(Path,'replace',failed)
    with pytest.raises(OSError) as raised:safe_paths.atomic_write(target,b'NEW')
    assert raised.value is original and len(calls)==1 and not retry_clock[1]
    assert target.read_bytes()==b'OLD'


def test_non_windows_errors_are_not_retried(tmp_path,monkeypatch,retry_clock):
    from sme_gend.services import file_contention as retry
    monkeypatch.setattr(retry,'_WINDOWS',False)
    def failed():raise win_error(32)
    with pytest.raises(OSError):retry.retry_file_operation(failed,path=tmp_path/'photo.jpg')
    assert not retry_clock[1]


@pytest.mark.skipif(os.name!='nt',reason='Windows read-only attribute')
def test_readonly_user_destination_keeps_its_permissions(tmp_path,retry_clock):
    import stat
    target=tmp_path/'photo.jpg';target.write_bytes(b'OLD');target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):safe_paths.atomic_write(target,b'NEW')
        assert target.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert target.read_bytes()==b'OLD' and not retry_clock[1]
        assert not list(tmp_path.glob('.sme-*'))
    finally:target.chmod(stat.S_IWRITE|stat.S_IREAD)


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive rename')
def test_exclusive_publication_does_not_replace_a_new_concurrent_file(tmp_path,monkeypatch,retry_clock):
    target=tmp_path/'photo.jpg';original=Path.rename;calls=[]
    def collision(path,dest):
        calls.append(path)
        if len(calls)==1:
            target.write_bytes(b'CONCURRENT');raise win_error(32)
        return original(path,dest)
    monkeypatch.setattr(Path,'rename',collision)
    with pytest.raises(FileExistsError):
        with safe_paths.atomic_destination(target,replace_existing=False) as temporary:
            temporary.write_bytes(b'PREPARED')
    assert len(calls)==2 and target.read_bytes()==b'CONCURRENT'
    assert not list(tmp_path.glob('.sme-*'))


@pytest.mark.parametrize('rollback',[False,True])
def test_restore_rechecks_external_changes_after_a_failed_rename(tmp_path,monkeypatch,retry_clock,rollback):
    from io import BytesIO
    from sme_gend.services.restore_files import RestoreFiles
    target=tmp_path/'config.json';target.write_bytes(b'OLD')
    files=RestoreFiles(tmp_path/'private');files.add(target,BytesIO(b'NEW'))
    if rollback:files.publish()
    original=Path.replace;calls=[]
    def changed(path,dest):
        if Path(dest)==target:
            calls.append(path)
            target.write_bytes(b'EXTERNAL');raise win_error(32)
        return original(path,dest)
    monkeypatch.setattr(Path,'replace',changed)
    with pytest.raises(RuntimeError,match='Retour' if rollback else 'changé'):
        files.rollback() if rollback else files.publish()
    assert len(calls)==1 and target.read_bytes()==b'EXTERNAL'
    assert not list(target.parent.glob('.sme-*'))


def test_restore_rechecks_external_changes_before_retrying_deletion(tmp_path,monkeypatch,retry_clock):
    from io import BytesIO
    from sme_gend.services.restore_files import RestoreFiles
    target=tmp_path/'new.json'
    files=RestoreFiles(tmp_path/'private');files.add(target,BytesIO(b'NEW'));files.publish()
    original=Path.unlink;calls=[]
    def changed(path,*args,**kwargs):
        if path==target:
            calls.append(path);target.write_bytes(b'EXTERNAL');raise win_error(32)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',changed)
    with pytest.raises(RuntimeError,match='Retour'):files.rollback()
    assert len(calls)==1 and target.read_bytes()==b'EXTERNAL'


def test_photo_revalidates_the_write_lease_before_retrying(tmp_path,monkeypatch,retry_clock):
    from contextlib import contextmanager
    from sme_gend.services import personnel_media,shared_media
    from sme_gend.services.shared_media import MediaWriteTarget
    monkeypatch.setattr(personnel_media,'personnel_media_root',lambda:tmp_path)
    lost=[False];checks=[]
    class Gate:
        def check(self):
            checks.append(True)
            if lost[0]:raise RuntimeError('synthetic lost write lease')
    @contextmanager
    def lease(*args,**kwargs):yield MediaWriteTarget(tmp_path,gate=Gate())
    monkeypatch.setattr(shared_media,'shared_media_write_target',lease)
    source=tmp_path/'source.jpg';source.write_bytes(b'NEW')
    target=tmp_path/'photos/42.jpg';target.parent.mkdir();target.write_bytes(b'OLD')
    calls=[]
    def denied(path,dest):
        calls.append(path);lost[0]=True;raise win_error(32)
    monkeypatch.setattr(Path,'replace',denied)
    with pytest.raises(RuntimeError,match='lost write lease') as raised:
        personnel_media.save_person_photo('42',source)
    assert not isinstance(raised.value,personnel_media.MediaWriteCommittedError)
    assert len(calls)==1 and len(checks)>=3 and target.read_bytes()==b'OLD'
    assert not list(tmp_path.rglob('.sme-*.part'))


def test_photo_retries_old_variant_cleanup_without_recopy(tmp_path,monkeypatch,retry_clock):
    from sme_gend.services import personnel_media
    monkeypatch.setattr(personnel_media,'personnel_media_root',lambda:tmp_path)
    source=tmp_path/'source.png';source.write_bytes(b'NEW')
    previous=tmp_path/'photos/42.jpg';previous.parent.mkdir();previous.write_bytes(b'OLD')
    original=Path.unlink;calls=[]
    def busy_once(path,*args,**kwargs):
        if path==previous:
            calls.append(path)
            if len(calls)==1:raise win_error(32)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',busy_once)
    target=personnel_media.save_person_photo('42',source)
    assert target.suffix=='.png' and target.read_bytes()==b'NEW'
    assert len(calls)==2 and not previous.exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows read-only attribute')
def test_copied_readonly_temporary_is_cleaned_without_changing_the_source(tmp_path):
    import stat
    source=tmp_path/'source.jpg';source.write_bytes(b'NEW');source.chmod(stat.S_IREAD)
    target=tmp_path/'photo.jpg';original=ValueError('synthetic invalid preparation')
    try:
        with pytest.raises(ValueError) as raised:
            with safe_paths.atomic_destination(target) as temporary:
                import shutil
                shutil.copy2(source,temporary);raise original
        assert raised.value is original and not list(tmp_path.glob('.sme-*'))
        assert source.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert source.read_bytes()==b'NEW' and not target.exists()
    finally:source.chmod(stat.S_IREAD|stat.S_IWRITE)


def test_failed_access_staging_copy_removes_its_partial_file(tmp_path,monkeypatch):
    from sme_gend.services import access_odbc
    staging=tmp_path/'staging';staging.mkdir()
    source=tmp_path/'synthetic.mdb';source.write_bytes(b'SYNTHETIC MDB')
    monkeypatch.setattr(access_odbc,'import_staging_dir',lambda:staging)
    def failed(src,dest):
        Path(dest).write_bytes(b'PARTIAL');raise OSError('synthetic copy failure')
    monkeypatch.setattr(access_odbc.shutil,'copy',failed)
    with pytest.raises(OSError,match='copy failure'):access_odbc.stage_access_file_copy(source)
    assert not list(staging.iterdir()) and source.read_bytes()==b'SYNTHETIC MDB'


@pytest.mark.skipif(os.name!='nt',reason='Windows exclusive rename')
def test_access_staging_keeps_a_fresh_mtime_and_retries_exclusive_rename(tmp_path,monkeypatch,retry_clock):
    from sme_gend.services import access_odbc
    staging=tmp_path/'staging';staging.mkdir()
    source=tmp_path/'synthetic.mdb';source.write_bytes(b'SYNTHETIC MDB');os.utime(source,(1,1))
    monkeypatch.setattr(access_odbc,'import_staging_dir',lambda:staging)
    original=Path.rename;calls=[]
    def busy_once(path,dest):
        calls.append(path)
        if len(calls)==1:raise win_error(32)
        return original(path,dest)
    monkeypatch.setattr(Path,'rename',busy_once)
    target=access_odbc.stage_access_file_copy(source)
    assert target.read_bytes()==source.read_bytes() and target.stat().st_mtime>1_000_000
    assert source.stat().st_mtime==1 and list(staging.iterdir())==[target] and len(calls)==2



@pytest.mark.skipif(os.name!='nt',reason='Windows read-only attribute')
def test_readonly_old_variant_reports_that_the_new_image_is_already_saved(tmp_path,monkeypatch,retry_clock):
    import stat
    from sme_gend.services import personnel_media
    monkeypatch.setattr(personnel_media,'personnel_media_root',lambda:tmp_path)
    source=tmp_path/'source.png';source.write_bytes(b'NEW')
    previous=tmp_path/'photos/42.jpg';previous.parent.mkdir();previous.write_bytes(b'OLD')
    previous.chmod(stat.S_IREAD)
    try:
        with pytest.raises(personnel_media.MediaWriteCommittedError) as raised:
            personnel_media.save_person_photo('42',source)
        assert raised.value.committed is True
        assert previous.read_bytes()==b'OLD' and previous.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
        assert (previous.parent/'42.png').read_bytes()==b'NEW'
        assert not retry_clock[1] and not list(tmp_path.rglob('.sme-*.part'))
    finally:previous.chmod(stat.S_IWRITE|stat.S_IREAD)
