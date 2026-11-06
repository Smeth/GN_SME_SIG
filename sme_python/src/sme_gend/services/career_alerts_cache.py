"""Cache et scan asynchrone des alertes carrière."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock, Event

from sme_gend.config.settings import Settings
from sme_gend.services.career_import_alerts import CareerAlert, scan_all_career_alerts


@dataclass(slots=True)
class CareerAlertsSnapshot:
    alerts: list[CareerAlert] = field(default_factory=list)
    warning_count: int = 0
    total: int = 0
    ready: bool = False
    scanning: bool = False
    mois: str = ""
    revision: str | None = None


#  Settings (dataclass frozen, champs hashables) comme clé par valeur — pas
#  id(settings), voir liste_generale_cache.py pour l'explication (réutilisation
#  d'adresse mémoire CPython sur des Settings clonés à la volée).
_cache: dict[tuple[Settings, str], CareerAlertsSnapshot] = {}
_lock = RLock()
_generation = 0
_inflight: dict[tuple[Settings, str], tuple[int, Event]] = {}
_WAIT_SECONDS = 30


def _cache_key(settings: Settings) -> tuple[Settings, str]:
    from sme_gend.config.liste_generale_scope import resolve_liste_view_mois

    return (settings, resolve_liste_view_mois(settings) or "")


def invalidate_career_alerts_cache(settings: Settings | None = None) -> None:
    global _generation
    with _lock:
        _generation += 1
        # All owners belong to the previous generation. Wake their waiters now;
        # an old owner must never clear or publish over a newer scan.
        for _, completed in _inflight.values():
            completed.set()
        _inflight.clear()
        for key in list(_cache):
            if settings is None or key[0] == settings:
                _cache.pop(key, None)


def get_career_alerts_snapshot(settings: Settings) -> CareerAlertsSnapshot:
    key = _cache_key(settings)
    with _lock:
        return _cache.get(key, CareerAlertsSnapshot())


def scan_and_cache_career_alerts(
    settings: Settings,
    *,
    mois_focus: str | None = None,
    limit: int | None = None,
    revision: str | None = None,
) -> CareerAlertsSnapshot:
    key = _cache_key(settings)
    # Le cache et le scan doivent décrire exactement le même mois.
    focus = mois_focus if mois_focus is not None else (key[1] or None)
    key = (settings, focus or "")
    while True:
        with _lock:
            generation = _generation
            cached = _cache.get(key)
            if revision is not None and cached and cached.ready and cached.revision == revision:
                return cached
            running = _inflight.get(key)
            owner = running is None or running[0] != generation
            if owner:
                completed = Event()
                _inflight[key] = (generation, completed)
                _cache[key] = CareerAlertsSnapshot(scanning=True, mois=key[1])
            else:
                completed = running[1]
        if not owner:
            if not completed.wait(timeout=_WAIT_SECONDS):
                raise TimeoutError("Le calcul des alertes est toujours en cours. Réessayez dans quelques instants.")
            with _lock:
                if generation != _generation:
                    continue
                snapshot = _cache.get(key)
                if snapshot and snapshot.ready:
                    if revision is not None and snapshot.revision != revision:
                        continue
                    return snapshot
            raise RuntimeError("Le calcul des alertes a été interrompu. Relancez l’actualisation.")
        try:
            alerts = scan_all_career_alerts(settings, mois_focus=focus, limit=None)
            snap = CareerAlertsSnapshot(
                alerts=alerts, warning_count=sum(a.severity == "warning" for a in alerts),
                total=len(alerts), ready=True, scanning=False, mois=key[1], revision=revision,
            )
            with _lock:
                if generation != _generation:
                    continue
                if _inflight.get(key) == (generation, completed):
                    _cache[key] = snap
            return snap
        finally:
            with _lock:
                if _inflight.get(key) == (generation, completed):
                    _inflight.pop(key, None)
                    snapshot = _cache.get(key)
                    if snapshot is not None and snapshot.scanning:
                        _cache[key] = CareerAlertsSnapshot(mois=key[1])
                completed.set()


def dossier_alerts_for_agent(settings: Settings, legacy_id: str):
    """Reuse the complete scan only while the shared database revision matches."""
    from sme_gend.services.data_sync import read_data_revision
    key = str(legacy_id or '').strip()
    if not key:
        return []
    for _ in range(3):
        revision = read_data_revision(settings)
        snapshot = scan_and_cache_career_alerts(settings, revision=revision)
        if revision == read_data_revision(settings):
            return [alert for alert in snapshot.alerts if alert.agent_legacy_id == key]
        invalidate_career_alerts_cache(settings)
    raise RuntimeError('Les données changent pendant le chargement ; réessayez la consultation.')
