"""Recoverable removal of this workstation's monthly archives and personnel media."""
import json
from pathlib import Path
import shutil
from sme_gend.services.restore_files import digest, write_recovery
from sme_gend.services.operation_progress import operation_progress


def reset_local_paths(settings, *, keep_document_history):
    from sme_gend.services.sme_month_closure import sme_archives_dir
    from sme_gend.services.backup_restore_service import _collect_personnel_media_roots
    from sme_gend.config.settings import project_root
    roots = [(sme_archives_dir(), ('sme_bundle_*.mdb', 'sme_bundle_*.accdb', 'liste_present_layout_*.json'))]
    if not keep_document_history:
        roots.append((sme_archives_dir()/'publications_sme', ('**/*',)))
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
        paths = tuple(paths)
        with operation_progress((('copy', 'Copie de récupération des fichiers locaux'),)) as progress:
            progress.start('copy', total=len(paths), detail=f'{len(paths)} fichier(s) à sauvegarder.')
            for number, path in enumerate(paths):
                path = Path(path).resolve()
                progress.update('copy', current=number, total=len(paths),
                                detail=f'Sauvegarde et vérification du fichier {number + 1}/{len(paths)} : {path.name}…')
                if path.is_relative_to(self.workspace.resolve()):
                    raise ValueError("La sauvegarde ne peut pas etre placee parmi les fichiers a effacer.")
                copy = self.workspace / f'{number}.original'
                before = digest(path)
                shutil.copy2(path, copy)
                if digest(copy) != before or digest(path) != before:
                    raise RuntimeError("Un fichier a change pendant sa sauvegarde.")
                self.entries.append({'path': str(path), 'copy': str(copy), 'sha256': before})
                progress.update('copy', current=number + 1, total=len(paths),
                                detail=f'Fichier {number + 1}/{len(paths)} sauvegardé et vérifié : {path.name}.')
            self.record()
            progress.finish('copy', detail=f'{len(paths)} fichier(s) sauvegardé(s) et vérifié(s).')

    def record(self):
        write_recovery(self.workspace / 'reset_files_recovery.json', json.dumps({'phase': self.phase, 'entries': self.entries}, indent=2).encode())

    def remove(self):
        self.phase = 'removing'
        self.record()
        with operation_progress((('remove', 'Retrait des fichiers locaux sauvegardés'),)) as progress:
            progress.start('remove', total=len(self.entries))
            for number, entry in enumerate(self.entries, 1):
                progress.update('remove', current=number - 1, total=len(self.entries),
                                detail=f'Contrôle et retrait du fichier {number}/{len(self.entries)} : {Path(entry["path"]).name}…')
                if digest(Path(entry['path'])) != entry['sha256']:
                    raise RuntimeError("Un fichier a change avant la réinitialisation.")
                path = Path(entry['path'])
                path.unlink()
                progress.update('remove', current=number, total=len(self.entries),
                                detail=f'Fichier {number}/{len(self.entries)} retiré : {path.name}.')
            self.phase = 'removed'
            self.record()
            progress.finish('remove', detail=f'{len(self.entries)} fichier(s) retiré(s).')

    def rollback(self):
        with operation_progress((('recover', 'Récupération des fichiers après annulation'),)) as progress:
            progress.start('recover', total=len(self.entries), detail='Annulation : récupération des fichiers locaux…')
            for number, entry in enumerate(self.entries, 1):
                path = Path(entry['path'])
                progress.update('recover', current=number - 1, total=len(self.entries),
                                detail=f'Récupération du fichier {number}/{len(self.entries)} : {path.name}…')
                if path.exists():
                    if digest(path) != entry['sha256']:
                        raise RuntimeError("Récupération manuelle nécessaire : fichier modifié entre-temps.")
                else:
                    shutil.copy2(entry['copy'], path)
                progress.update('recover', current=number, total=len(self.entries),
                                detail=f'Fichier {number}/{len(self.entries)} récupéré : {path.name}.')
            self.phase = 'rolled_back'
            self.record()
            progress.finish('recover', detail='Fichiers locaux récupérés ; opération annulée.')

    def committed(self):
        self.phase = 'committed'
        self.record()
