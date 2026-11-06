"""
Cycle de vie des pages SME — pattern « entreprise » :

- Charger les données une fois (ou après invalidation explicite).
- Réafficher la même page sans relire MySQL / disque.
- Invalidation : changement de mois, config, action utilisateur (F5, Enregistrer).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtGui import QShowEvent

from sme_gend.ui.bg_task import run_in_background

T = TypeVar("T")


class ViewDataState:
    """État de chargement d'une vue (thread UI uniquement)."""

    __slots__ = ("_ready", "_loading", "_stale", "_revision")

    def __init__(self, *, stale: bool = True) -> None:
        self._ready = False
        self._loading = False
        self._stale = stale
        self._revision = 0

    def mark_stale(self) -> None:
        self._revision += 1
        self._stale = True
        self._ready = False

    @property
    def revision(self) -> int:
        """Version du contexte : une réponse antérieure ne peut plus le valider."""
        return self._revision

    def is_ready(self) -> bool:
        return self._ready and not self._stale

    def needs_load(self) -> bool:
        return self._stale or not self._ready

    def begin_load(self) -> bool:
        if self._loading or not self.needs_load():
            return False
        self._loading = True
        return True

    def finish_load(self) -> None:
        self._loading = False
        self._ready = True
        self._stale = False

    def fail_load(self) -> None:
        self._loading = False


class CachedViewMixin:
    """
    Mixin : ``mark_dirty`` invalide ; ``showEvent`` / ``on_page_activated`` ne rechargent que si besoin.
    """

    _view_data: ViewDataState

    def _init_view_cache(self, *, stale: bool = True) -> None:
        self._view_data = ViewDataState(stale=stale)

    def mark_view_stale(self) -> None:
        self._view_data.mark_stale()

    def mark_dirty(self) -> None:
        self._view_data.mark_stale()

    def view_data_ready(self) -> bool:
        return self._view_data.is_ready()

    def on_page_activated(self) -> None:
        self._ensure_view_data_loaded()

    def _after_show_event(self, event: QShowEvent) -> bool:
        return not event.spontaneous()

    def _ensure_view_data_loaded(self, *, force: bool = False) -> None:
        if force:
            self._view_data.mark_stale()
        if not self._view_data.needs_load():
            return
        self._load_view_data()

    def _load_view_data(self) -> None:
        if not self._view_data.begin_load():
            return
        try:
            self._fetch_and_apply_view_data()
            self._view_data.finish_load()
        except Exception:
            self._view_data.fail_load()
            raise

    def _fetch_and_apply_view_data(self) -> None:
        raise NotImplementedError

    def _load_view_data_async(
        self,
        work: Callable[[], T],
        apply: Callable[[T], None],
        *,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        if not self._view_data.begin_load():
            return

        def done(result: T) -> None:
            try:
                apply(result)
                self._view_data.finish_load()
            except BaseException as exc:
                self._view_data.fail_load()
                if on_error is not None:
                    on_error(exc)
                else:
                    raise

        def fail(exc: BaseException) -> None:
            self._view_data.fail_load()
            if on_error is not None:
                on_error(exc)

        run_in_background(self, work, done, on_error=fail)
