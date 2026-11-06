"""Interprocess JSONL append; durable delivery is owned by audit_mirror_recovery."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
from sme_gend.repositories.mysql_connection import after_mysql_commit

_append_lock=threading.RLock()


@contextmanager
def locked_mirror_stream(path, *, create=True, thread_lock=None):
    with (_append_lock if thread_lock is None else thread_lock):
        path=Path(path)
        if create:path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a+b' if create else 'r+b',buffering=0) as stream:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
                def unlock():
                    stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                def unlock():fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
            try:yield stream
            finally:unlock()


def write_all(stream,payload):
    view=memoryview(payload)
    while view:
        written=stream.write(view)
        if not written:raise OSError('Écriture du miroir local interrompue.')
        view=view[written:]


def append_json_mirror(path:Path,entries):
    payload=''.join(json.dumps(entry,ensure_ascii=False,default=str)+'\n' for entry in entries).encode('utf-8')
    if not payload:return
    def append():
        with locked_mirror_stream(path) as stream:
            from sme_gend.services.audit_mirror_recovery import _repair_tail
            _repair_tail(stream,Path(path))
            stream.seek(0,os.SEEK_END);write_all(stream,payload)
            stream.flush();os.fsync(stream.fileno())
    after_mysql_commit(append)
