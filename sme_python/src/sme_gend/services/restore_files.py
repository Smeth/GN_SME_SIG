"""Stage restored files privately; compensate completed publications on failure."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import os
from sme_gend.services.safe_paths import atomic_destination
from sme_gend.services.mysql_private_options import _restrict_folder


def write_recovery(path,data):
    """Flush the journal before the operation it describes can be published."""
    with atomic_destination(path) as temporary:
        with temporary.open('wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())


def digest(path):
    if not path.exists():return None
    if not path.is_file():raise ValueError('Une destination de restauration n’est pas un fichier.')
    result=sha256()
    with path.open('rb') as source:
        for block in iter(lambda:source.read(1024*1024),b''):result.update(block)
    return result.hexdigest()


_UNSET = object()


class RestoreFiles:
    def __init__(self,workspace):
        self.workspace=Path(workspace);self.entries=[];self.targets=set();self.phase='preparing'
        self.workspace.mkdir(parents=True)
        _restrict_folder(self.workspace)  # Before copying archives or originals.
        (self.workspace/'files').mkdir()
        self.record()

    def record(self):
        data=json.dumps(dict(phase=self.phase,entries=self.entries),indent=2).encode('utf-8')
        write_recovery(self.workspace/'files_recovery.json',data)

    def add(self,target,source,*,expected_before=_UNSET):
        target=Path(target).resolve()
        if str(target).casefold() in self.targets:raise ValueError('Plusieurs composants restaurent le même fichier.')
        self.targets.add(str(target).casefold())
        number=len(self.entries)
        staged=self.workspace/'files'/f'{number}.new';original=self.workspace/'files'/f'{number}.old'
        previous=digest(target)
        if expected_before is not _UNSET and previous!=expected_before:
            raise RuntimeError("Les chemins locaux ont changé pendant leur lecture ; restauration annulée.")
        if previous is not None:
            shutil.copyfile(target,original)
            if digest(original)!=previous:raise RuntimeError('Un fichier a changé pendant sa copie de sécurité.')
        with staged.open('wb') as output:shutil.copyfileobj(source,output)
        # Exercise destination creation/permissions before changing MySQL.
        import tempfile
        target.parent.mkdir(parents=True,exist_ok=True)
        descriptor,probe=tempfile.mkstemp(prefix='.sme-restore-probe-',dir=target.parent)
        os.close(descriptor)
        Path(probe).unlink()
        entry=dict(target=str(target),staged=str(staged),original=str(original),before=previous,
            after=digest(staged),publishing=False,published=False)
        self.entries.append(entry);self.record()

    def _expect_digest(self,target,expected):
        if digest(target)!=expected:
            raise RuntimeError("Un fichier a changé pendant l’attente de publication ; remplacement refusé.")

    def publish(self):
        self.phase='publishing';self.record()
        for entry in self.entries:
            target=Path(entry['target'])
            if digest(target)!=entry['before']:raise RuntimeError('Un fichier a changé pendant la préparation ; restauration annulée.')
            entry['publishing']=True;self.record()
            with atomic_destination(target,replace_existing=entry['before'] is not None,
                                    before_publish=lambda:self._expect_digest(target,entry['before'])) as temporary:
                shutil.copyfile(entry['staged'],temporary)
            entry['published']=True;self.record()
        self.phase='published';self.record()

    def rollback(self):
        failures=[]
        for entry in reversed(self.entries):
            if not entry['publishing']:continue
            try:
                target=Path(entry['target']);now=digest(target)
                if now==entry['before']:continue
                if now!=entry['after']:raise RuntimeError('Fichier modifié depuis la publication ; retour automatique refusé.')
                if entry['before'] is None:
                    from sme_gend.services.file_contention import retry_file_operation
                    retry_file_operation(lambda:target.unlink(),path=target,
                        before_attempt=lambda:self._expect_digest(target,entry['after']))
                else:
                    if digest(Path(entry['original']))!=entry['before']:
                        raise RuntimeError('Copie de sécurité de fichier altérée ; récupération automatique refusée.')
                    with atomic_destination(target,before_publish=lambda:self._expect_digest(target,entry['after'])) as temporary:
                        shutil.copyfile(entry['original'],temporary)
                entry['published']=False;entry['publishing']=False
            except Exception as exc:failures.append(exc)
        self.phase='recovery_required' if failures else 'rolled_back';self.record()
        if failures:raise RuntimeError('Retour de certains fichiers impossible ; conserver la maintenance et le dossier de récupération.') from failures[0]

    def complete(self):
        self.phase='completed';self.record()
