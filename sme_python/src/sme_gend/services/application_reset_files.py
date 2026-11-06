"""Recoverable removal of this workstation's monthly archives and personnel media."""
import json
from pathlib import Path
import shutil
from sme_gend.services.restore_files import digest, write_recovery


def reset_local_paths(settings, *, keep_document_history):
    from sme_gend.services.sme_month_closure import sme_archives_dir
    from sme_gend.services.backup_restore_service import _collect_personnel_media_roots
    from sme_gend.config.settings import project_root
    roots = [(sme_archives_dir(), ('sme_bundle_*.mdb', 'sme_bundle_*.accdb', 'liste_present_layout_*.json'))]
    for root in _collect_personnel_media_roots(settings):
        roots.extend((root / sub, ('**/*',)) for sub in ('photos', 'grades'))
    paths = set()
    for root, patterns in roots:
        root = Path(root).resolve()
        for pattern in patterns:
            for path in root.glob(pattern):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Lien externe dans les fichiers a réinitialiser : operation annulée.")
                if path.is_file():
                    paths.add(path.resolve())
    data = project_root() / 'data'
    for pattern in ('personnel_audit*.jsonl', 'sensitive_audit_log.jsonl', 'user_activity.jsonl') + (() if keep_document_history else ('doc_gen_log.jsonl',)):
        for path in data.glob(pattern):
            if path.is_symlink():
                raise ValueError("Lien externe dans les journaux locaux.")
            if path.is_file():
                paths.add(path.resolve())
    return sorted(paths)


class ResetLocalFiles:
    def __init__(self, workspace, paths):
        from sme_gend.services.mysql_private_options import _restrict_folder
        self.workspace = Path(workspace)
        self.workspace.mkdir(exist_ok=False)
        _restrict_folder(self.workspace)
        self.entries = []
        self.phase = 'prepared'
        for number, path in enumerate(paths):
            path = Path(path).resolve()
            if path.is_relative_to(self.workspace.resolve()):
                raise ValueError("La sauvegarde ne peut pas etre placee parmi les fichiers a effacer.")
            copy = self.workspace / f'{number}.original'
            before = digest(path)
            shutil.copy2(path, copy)
            if digest(copy) != before or digest(path) != before:
                raise RuntimeError("Un fichier a change pendant sa sauvegarde.")
            self.entries.append({'path': str(path), 'copy': str(copy), 'sha256': before})
        self.record()

    def record(self):
        write_recovery(self.workspace / 'reset_files_recovery.json', json.dumps({'phase': self.phase, 'entries': self.entries}, indent=2).encode())

    def remove(self):
        self.phase = 'removing'
        self.record()
        for entry in self.entries:
            if digest(Path(entry['path'])) != entry['sha256']:
                raise RuntimeError("Un fichier a change avant la réinitialisation.")
            Path(entry['path']).unlink()
        self.phase = 'removed'
        self.record()

    def rollback(self):
        for entry in self.entries:
            path = Path(entry['path'])
            if path.exists():
                if digest(path) != entry['sha256']:
                    raise RuntimeError("Récupération manuelle nécessaire : fichier modifié entre-temps.")
            else:
                shutil.copy2(entry['copy'], path)
        self.phase = 'rolled_back'
        self.record()

    def committed(self):
        self.phase = 'committed'
        self.record()
