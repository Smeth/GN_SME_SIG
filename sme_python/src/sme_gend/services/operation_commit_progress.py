"""Complete a write operation only after its owning transaction commits."""
from sme_gend.repositories.mysql_connection import after_mysql_commit, has_import_transaction


def finish_after_commit(settings, progress, key, *, detail):
    if has_import_transaction(settings):
        progress.update(key, detail='Traitement préparé ; attente de validation de la transaction appelante…')
        after_mysql_commit(lambda: progress.finish(key, detail=detail))
    else:
        progress.finish(key, detail=detail)
