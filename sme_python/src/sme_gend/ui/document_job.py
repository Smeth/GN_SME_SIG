"""Document jobs: immutable input snapshots, responsive UI and unique previews."""
from pathlib import Path
import tempfile
from uuid import uuid4


def preview_document(name: str) -> Path:
    # Keep the directory alive: Word may still be using it after the UI returns.
    directory = Path(tempfile.gettempdir()) / ("sme-document-" + uuid4().hex)
    directory.mkdir()
    return directory / name


def can_export() -> bool:
    from sme_gend.services.auth_guard import session_can_export_documents
    return session_can_export_documents()


def require_export(parent) -> bool:
    from sme_gend.services.auth_guard import require_permission
    return require_permission(parent, "effectifs.export")


def run_document_job(parent, fn):
    from sme_gend.ui.bg_task import run_with_progress
    def work():
        from sme_gend.services.operation_progress import operation_progress
        with operation_progress((('prepare', 'Préparer le document'),
                                 ('generate', 'Générer et enregistrer le document'),
                                 ('finish', 'Confirmer la fin de la génération'))) as progress:
            progress.start('prepare')
            progress.finish('prepare')
            progress.start('generate')
            fn()
            progress.finish('generate')
            progress.finish('finish')
            return True
    return run_with_progress(parent, "Documents", "Génération du document en cours…", work)
