"""Cache mémoire de la liste générale (évite les relectures MySQL à chaque écran)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar
from threading import RLock

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.config.liste_generale_scope import resolve_export_mois, resolve_liste_view_mois

T = TypeVar("T")

#  Settings (dataclass frozen, champs hashables) comme clé par valeur — pas
#  id(settings), qui pouvait pointer vers un autre objet Settings après
#  réutilisation d'adresse mémoire par CPython (Settings clonés à la volée via
#  dataclasses.replace) et renvoyer silencieusement les lignes d'un autre mois/base.
_rows_cache: dict[tuple[Settings, str], list[ListeGeneraleRow]] = {}
_rows_lock = RLock()
_rows_generation = 0


def liste_cache_key(settings: Settings) -> tuple[Settings, str]:
    view = resolve_liste_view_mois(settings) or ""
    return (settings, view)


def get_liste_rows(
    settings: Settings,
    loader: Callable[[], list[ListeGeneraleRow]],
) -> list[ListeGeneraleRow]:
    return get_liste_rows_by_key(liste_cache_key(settings), loader)


def numerique_cache_key(settings: Settings) -> tuple[Settings, str]:
    ym = resolve_export_mois(settings) or ""
    return (settings, f"numerique_snapshot:{ym}")


def cached_numerique_liste_rows(settings: Settings) -> list[ListeGeneraleRow]:
    """
    Liste générale pour situation numérique — même effectif que l'écran Liste générale.

    Matrice, indispo/structure et listes détail partagent ce cache (``cached_liste_rows``).
    """
    return cached_liste_rows(settings)


def get_liste_rows_by_key(
    key: tuple[Settings, str],
    loader: Callable[[], list[ListeGeneraleRow]],
) -> list[ListeGeneraleRow]:
    with _rows_lock:
        hit = _rows_cache.get(key)
        generation = _rows_generation
        if hit is not None:
            return hit
    rows = loader()
    with _rows_lock:
        if generation == _rows_generation:
            _rows_cache[key] = rows
    return rows


def cached_liste_rows(settings: Settings) -> list[ListeGeneraleRow]:
    """
    Liste générale triée (même règle que l'écran personnel) — cache partagé UI / exports.
    """
    from sme_gend.repositories.grade_repository import GradeRepository
    from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
    from sme_gend.services.personnel_list_order import sort_liste_generale_by_grade

    def _load() -> list[ListeGeneraleRow]:
        raw = ListeGeneraleRepository(settings).fetch_all()
        grades = GradeRepository(settings).fetch_all()
        return sort_liste_generale_by_grade(raw, grades)

    return get_liste_rows_by_key(liste_cache_key(settings), _load)


def invalidate_liste_cache(settings: Settings | None = None) -> None:
    """À appeler après toute écriture MySQL sur la liste générale / agents."""
    global _rows_generation
    with _rows_lock:
        _rows_generation += 1
        if settings is None:
            _rows_cache.clear()
        else:
            stale = [k for k in _rows_cache if k[0] == settings]
            for k in stale:
                _rows_cache.pop(k, None)
    try:
        from sme_gend.services.career_alerts_cache import invalidate_career_alerts_cache

        invalidate_career_alerts_cache(settings)
    except Exception:
        pass
    _bump_dashboard_snapshot_epoch()


_snapshot_epoch = 0


def invalidate_dashboard_snapshot() -> None:
    """Force le prochain affichage du tableau de bord à relire gains, pertes et décès."""
    _bump_dashboard_snapshot_epoch()


def dashboard_snapshot_epoch() -> int:
    return _snapshot_epoch


def _bump_dashboard_snapshot_epoch() -> None:
    global _snapshot_epoch
    _snapshot_epoch += 1
