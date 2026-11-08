"""Measured operation phases, independent from Qt and database transactions.

Percentages describe completed phases/work units, never elapsed-time guesses.
Nested operations contribute to their parent's active phase instead of resetting
the overall progress. Observers cannot interrupt a business transaction.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math

_sink = ContextVar('operation_progress_sink', default=None)
_current = ContextVar('operation_progress_current', default=None)
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgressStep:
    key: str
    label: str
    state: str


@dataclass(frozen=True)
class ProgressSnapshot:
    steps: tuple[ProgressStep, ...]
    active_key: str
    percent: int
    detail: str
    current: int | float | None = None
    total: int | float | None = None


@contextmanager
def progress_reporting(callback):
    token = _sink.set(callback)
    try:
        yield
    finally:
        _sink.reset(token)


def current_operation_progress():
    return _current.get()


class operation_progress:
    def __init__(self, steps, *, include_substeps=False):
        self._steps = tuple((str(key), str(label)) for key, label in steps)
        if not self._steps or len({key for key, _ in self._steps}) != len(self._steps):
            raise ValueError('Les étapes de progression doivent avoir des identifiants uniques.')
        self._fractions = [0.0] * len(self._steps)
        self._finished = [False] * len(self._steps)
        self._active = 0
        self._started = False
        self._detail = ''
        self._completed_units = self._total_units = None
        self._parent = None
        self._parent_index = None
        self._child_span = None
        self._parent_span = (0.0, 1.0)
        self._token = None
        self._include_substeps = include_substeps
        self._children = {}

    def __enter__(self):
        self._parent = _current.get()
        if self._parent is not None:
            self._parent_index = self._parent._active
        if self._parent is not None and self._parent._child_span is not None:
            self._parent_span = self._parent._child_span
        self._token = _current.set(self)
        self._emit()
        return self

    def __exit__(self, *exc):
        _current.reset(self._token)
        return False  # Progress tracking never changes success/failure semantics.

    def _index(self, key):
        return next(index for index, step in enumerate(self._steps) if step[0] == key)

    def start(self, key, *, total=None, detail=''):
        index = self._index(key)
        if index < self._active:
            raise ValueError('Une étape terminée ne peut pas être redémarrée.')
        for previous in range(index):
            self._fractions[previous] = 1.0
            self._finished[previous] = True
        self._active = index
        self._started = True
        self._completed_units = 0 if total is not None else None
        self._total_units = None if total is None else max(0, total)
        self._detail = str(detail)
        self._emit()

    def update(self, key, *, current=None, total=None, detail=''):
        index = self._index(key)
        if index != self._active or not self._started:
            self.start(key, total=total, detail=detail)
        if total is not None:
            self._total_units = max(0, total)
        if current is not None:
            self._completed_units = max(0, current)
        if self._total_units and self._completed_units is not None:
            fraction = min(1.0, self._completed_units / self._total_units)
            self._fractions[index] = max(self._fractions[index], fraction)
        self._detail = str(detail)
        self._emit()

    @contextmanager
    def subtask(self, key, *, current, total, detail=''):
        """Allocate one of N nested jobs, e.g. restoring each selected month."""
        if total <= 0:
            raise ValueError('Nombre de sous-opérations invalide.')
        self.update(key, current=current, total=total, detail=detail)
        previous = self._child_span
        self._child_span = (min(1.0, current/total), min(1.0, (current+1)/total))
        try:
            yield
        except BaseException:
            raise
        else:
            self.update(key, current=current+1, total=total, detail=detail)
        finally:
            self._child_span = previous

    def finish(self, key, *, detail=''):
        index = self._index(key)
        if index != self._active or not self._started:
            self.start(key)
        self._fractions[index] = 1.0
        self._finished[index] = True
        if self._total_units is not None:
            self._completed_units = self._total_units
        self._detail = str(detail)
        self._emit()

    def _emit(self):
        fraction = sum(self._fractions) / len(self._fractions)
        if self._parent is not None:
            parent = self._parent
            begin, end = self._parent_span
            index = self._parent_index
            parent._fractions[index] = max(parent._fractions[index], begin+(end-begin)*fraction)
            label = self._steps[self._active][1]
            if index == parent._active and not parent._finished[index]:
                parent._detail = label + (' — '+self._detail if self._detail else '')
                parent._completed_units = self._completed_units
                parent._total_units = self._total_units
            parent._children[index] = self._expanded_steps()
            parent._emit()
            return
        callback = _sink.get()
        if callback is None:
            return
        steps = tuple(ProgressStep(key, label,
            'done' if self._finished[index] else 'running' if index == self._active and self._started else 'pending')
            for index, (key, label) in enumerate(self._steps))
        active_key = self._steps[self._active][0]
        if self._include_substeps:
            steps, active_key = self._expanded_steps()
        snapshot = ProgressSnapshot(steps, active_key,
                                    min(100 if all(self._finished) else 99, math.floor(100*fraction + 1e-8)), self._detail,
                                    self._completed_units, self._total_units)
        try:
            callback(snapshot)
        except Exception:
            _log.warning('Affichage de progression indisponible ; traitement poursuivi.')

    def _expanded_steps(self):
        """Keep the real service phases visible inside the generic UI job."""
        steps = []
        active_key = self._steps[self._active][0]
        for index, (key, label) in enumerate(self._steps):
            state = 'done' if self._finished[index] else 'running' if index == self._active and self._started else 'pending'
            children = self._children.get(index)
            if children:
                nested, nested_active = children
                steps.extend(ProgressStep(key+'/'+step.key, step.label,
                    'done' if self._finished[index] else step.state) for step in nested)
                if index == self._active:
                    active_key = key+'/'+nested_active
                    if not self._finished[index] and all(step.state=='done' for step in nested):
                        steps.append(ProgressStep(key,label,'running'))
                        active_key = key
            else:
                steps.append(ProgressStep(key, label, state))
        return tuple(steps), active_key
