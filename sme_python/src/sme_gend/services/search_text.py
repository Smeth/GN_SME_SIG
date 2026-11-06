"""Literal text search normalization; never changes stored business values."""
from datetime import date,datetime
import unicodedata
from functools import lru_cache

def normalize_search_text(value) -> str:
    text = "" if value is None else str(value)
    folded = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())

@lru_cache(maxsize=512)
def _normalized_query(needle: str) -> str:
    return normalize_search_text(needle)


def _build_search_blob(values) -> str:
    parts = []
    for value in values:
        if value is None:
            continue
        parts.append(str(value))
        if isinstance(value, (date, datetime)):
            parts.append(value.strftime("%d/%m/%Y"))
    return normalize_search_text(" ".join(parts))


@lru_cache(maxsize=65536)
def _search_blob(values: tuple) -> str:
    # Values, never row IDs alone: editing a cell cannot reuse its previous text.
    return _build_search_blob(values)


def search_values(needle: str, *values) -> bool:
    query = _normalized_query(str(needle or ""))
    if not query:
        return True
    try:
        blob = _search_blob(values)
    except TypeError:
        # Compatibility with unhashable imported values; no state is cached for them.
        blob = _build_search_blob(values)
    return query in blob
