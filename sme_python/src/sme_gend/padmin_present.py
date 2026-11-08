"""Critère « compte comme présent » — ref_position_admin + repli texte."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from threading import RLock

from sme_gend.models.padmin_ref import PadminRefRow

# Modes pilotés par ``presences_mode`` (``SmeContext``) :
# * ``"present"``       — ref ``compte_comme_present`` ou mot « présent » si hors référentiel
# * ``"stage_interne"`` — libellé « stage interne »
# * ``"les_deux"``      — présent (ref ou texte) ou stage interne

PRESENCES_MODES: tuple[str, ...] = ("present", "stage_interne", "les_deux")

_index_cache: dict[str, PadminPresenceIndex] = {}
_index_lock = RLock()
_index_generation = 0
_default_index = (0.0, None)


def _norm_label(s: str | None) -> str:
    value = " ".join((s or "").split()).casefold()
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))


def _collapsed(padmin: str | None) -> str:
    return " ".join((padmin or "").strip().split()).casefold().replace("é", "e").replace("è", "e")


def _non_present_exact_codes() -> frozenset[str]:
    from sme_gend.services import numerique_padmin as npa
    from sme_gend.services.position_codes import LABELS

    return frozenset(_collapsed(label) for code, labels in LABELS.items()
                     if code not in ('present', 'stage_interne') for label in labels) | frozenset(
        _collapsed(c)
        for c in (
            npa.PADMIN_DETACHE,
            npa.PADMIN_DESERTEUR,
            npa.PADMIN_CONGE_ADMIN,
            npa.PADMIN_CONGE_EXP,
            npa.PADMIN_MISSION,
            npa.PADMIN_MALADE,
            npa.PADMIN_INST_MR,
            npa.PADMIN_STAGE_LOCAL,
            npa.PADMIN_STAGE_EX,
            npa.PADMIN_PUNIS,
            npa.PADMIN_PRISON_MA,
        )
    )


def _is_explicit_non_present(padmin: str | None) -> bool:
    """Rubriques satellite NUMERIQUE — jamais comptées « présent »."""
    raw = (padmin or "").strip()
    if not raw:
        return False
    col = _collapsed(raw)
    if re.search(r"(^|[^a-z])(non[ -]*present|pas[ -]+present|absent)([^a-z]|$)", col):
        return True
    if col in _non_present_exact_codes():
        return True
    if "mission" in col and col != "mission":
        return True
    return False


def _is_default_present_au_corps(padmin: str | None) -> bool:
    """Convention Access/SME : Padmin vide = présent au corps."""
    return not (padmin or "").strip()


def _is_present_text(padmin: str | None) -> bool:
    """Vrai si le libellé contient le mot « présent » (hors référentiel)."""
    raw = (padmin or "").strip()
    if not raw:
        return False
    if _is_explicit_non_present(raw):
        return False
    norm = raw.lower().replace("é", "e").replace("è", "e")
    if re.search(r"(^|[^a-z])(non[ -]*present|pas[ -]+present|absent)([^a-z]|$)", norm):
        return False
    return bool(re.search(r"(^|[^a-z])present([^a-z]|$)", norm))


def _is_stage_interne(padmin: str | None) -> bool:
    col = _collapsed(padmin)
    if not col:
        return False
    return col in ("stageinterne", "stage interne")


def infer_compte_comme_present(libelle: str | None) -> bool:
    """
    Déduit ``compte_comme_present`` pour ``ref_position_admin`` à la création / import.
    """
    if _is_default_present_au_corps(libelle):
        return True
    if _is_explicit_non_present(libelle):
        return False
    if _is_stage_interne(libelle):
        return False
    return _is_present_text(libelle)


@dataclass(frozen=True, slots=True)
class PadminPresenceIndex:
    """Index libellé normalisé → compte_comme_present (ref_position_admin)."""

    by_libelle: dict[str, bool]

    def __post_init__(self):
        object.__setattr__(self, 'by_libelle', {_norm_label(k): v for k, v in self.by_libelle.items()})

    @classmethod
    def from_rows(cls, rows: list[PadminRefRow]) -> PadminPresenceIndex:
        m: dict[str, bool] = {}
        for r in rows:
            lb = _norm_label(r.libelle)
            if lb:
                m[lb] = bool(r.compte_comme_present)
        return cls(by_libelle=m)

    def compte_comme_present(self, padmin: str | None) -> bool | None:
        """True/False si le libellé est dans le référentiel ; None si inconnu."""
        key = _norm_label(padmin)
        if not key:
            return None
        if key in self.by_libelle:
            return self.by_libelle[key]
        return None


def _cache_key(settings) -> str:
    s = settings
    return f"{s.mysql_host}:{s.mysql_port}:{s.mysql_database}"


def invalidate_padmin_presence_cache(settings=None) -> None:
    global _index_generation, _default_index
    with _index_lock:
        _default_index = (0.0, None)
        _index_generation += 1
        if settings is None:
            _index_cache.clear()
        else:
            _index_cache.pop(_cache_key(settings), None)


def default_presence_index():
    """One shared index for legacy Python exports, never one SQL call per agent."""
    from time import monotonic
    global _default_index
    now = monotonic()
    with _index_lock:
        if now < _default_index[0]:
            return _default_index[1]
        generation = _index_generation
    try:
        from sme_gend.config import get_settings
        index = load_padmin_presence_index(get_settings())
    except Exception:
        index = None
    with _index_lock:
        if generation == _index_generation:
            _default_index = (now + 5, index)
    return index


def load_padmin_presence_index(settings) -> PadminPresenceIndex | None:
    """Charge ref_position_admin ; None si MySQL indisponible."""
    try:
        from sme_gend.repositories.mysql_bundle_repositories import MySQLPadminRepository

        key = _cache_key(settings)
        with _index_lock:
            if key in _index_cache:
                return _index_cache[key]
            generation = _index_generation
        rows = MySQLPadminRepository(settings).fetch_all()
        idx = PadminPresenceIndex.from_rows(rows)
        with _index_lock:
            if generation == _index_generation:
                _index_cache[key] = idx
        return idx
    except Exception:
        return None


def padmin_counts_as_present(
    padmin: str | None,
    mode: str = "present",
    *,
    index: PadminPresenceIndex | None = None,
    settings=None,
) -> bool:
    """
    True si la position administrative correspond au mode.

    Présent reste présent ; Stage interne est distinct. Le référentiel sert aux
    autres libellés, sans transformer une absence ou un stage en présence.
    """
    if index is None and settings is not None:
        index = load_padmin_presence_index(settings)

    if mode == "stage_interne":
        return _is_stage_interne(padmin)
    if _is_default_present_au_corps(padmin):
        return True
    if _is_stage_interne(padmin):
        return mode != "present"
    if _is_explicit_non_present(padmin):
        return False
    if _is_present_text(padmin):
        return True
    ref_flag = index.compte_comme_present(padmin) if index is not None else None
    present = ref_flag if ref_flag is not None else infer_compte_comme_present(padmin)
    if mode == "present":
        return present
    return present or _is_stage_interne(padmin)


def is_explicit_present_position(padmin: str | None) -> bool:
    """Strict list-of-present rule, independent from legacy/reference flags."""
    from sme_gend.services.position_codes import position_code

    return position_code(padmin) == 'present'
