"""Run reference I/O off the GUI thread while keeping exception handling local."""
def run_reference_task(parent,title,work):
    from sme_gend.ui.bg_task import run_with_progress
    def execute():
        try:return True,work()
        except Exception as exc:return False,exc
    result=run_with_progress(parent,title,"Traitement en cours…",execute,silent=True)
    if result is None:raise RuntimeError("Le traitement n'a pas pu être confirmé.")
    success,value=result
    if not success:raise value
    return value
