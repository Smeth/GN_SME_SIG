"""Measured export phases shared by Excel, Word and HTML generators."""

from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from inspect import signature

from sme_gend.services.operation_progress import operation_progress

EXPORT_STEPS = (
    ("prepare", "Préparer les données et le modèle"),
    ("render", "Remplir les lignes et les feuilles"),
    ("write", "Enregistrer le document"),
    ("finalize", "Finaliser et journaliser l’export"),
)
_export = ContextVar("measured_export", default=None)
HTML_STEPS = (("prepare", "Lire les données du document"),
              ("render", "Construire les tableaux et les pages"),
              ("finalize", "Finaliser le contenu du document"))


def tracked_export(fn=None, *, steps=EXPORT_STEPS):
    def decorate(function):
        parameters = signature(function)
        @wraps(function)
        def measured(*args, **kwargs):
            with operation_progress(steps) as progress:
                arguments = parameters.bind(*args, **kwargs).arguments
                rows = arguments.get('rows')
                hint = len(rows) if rows is not None and hasattr(rows, '__len__') else None
                state = {"progress": progress, "key": steps[0][0], "current": 0, "total": None, "rows_hint": hint}
                token = _export.set(state)
                try:
                    progress.start(state["key"])
                    result = function(*args, **kwargs)
                    progress.finish(steps[-1][0])
                    return result
                finally:
                    _export.reset(token)
        return measured
    return decorate(fn) if fn is not None else decorate


def export_phase(key, *, total=None, detail=""):
    state = _export.get()
    if state is not None:
        state.update(key=key, current=0, total=total)
        state["progress"].start(key, total=total, detail=detail)


def export_count(current, *, total=None, detail=""):
    state = _export.get()
    if state is not None:
        state["current"] = current
        if total is not None:
            state["total"] = total
        state["progress"].update(state["key"], current=current, total=state["total"], detail=detail)


def export_detail(detail):
    state = _export.get()
    if state is not None:
        state["progress"].update(state["key"], detail=detail)


@contextmanager
def export_subtask(current, total, *, detail=""):
    state = _export.get()
    if state is None:
        yield
    else:
        with state["progress"].subtask(state["key"], current=current, total=total, detail=detail):
            yield


def export_rows(rows, *, label="Lignes", total=None):
    """Report rows after they were written; limit Qt events on large exports."""
    state = _export.get()
    if state is None:
        yield from rows
        return
    if state["key"] == "prepare":
        export_phase("render", total=total if total is not None else state["rows_hint"])
    count = 0
    for count, row in enumerate(rows, 1):
        yield row
        state["current"] += 1
        if count == 1 or count % 100 == 0:
            detail = f"{label} : {count}" + (f" / {len(rows)}" if hasattr(rows, '__len__') else '') + " ligne(s) traitée(s)"
            if state["total"] is None:
                export_detail(detail)
            else:
                export_count(min(state["current"], state["total"]), detail=detail)
    detail = f"{label} : {count}" + (f" / {len(rows)}" if hasattr(rows, '__len__') else '') + " ligne(s) traitée(s)"
    if state["total"] is None:
        export_detail(detail)
    else:
        export_count(min(state["current"], state["total"]), detail=detail)
