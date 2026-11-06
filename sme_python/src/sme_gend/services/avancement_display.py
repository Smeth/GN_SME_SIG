"""Affichage des grades en abréviation (codes TabGrade) — module avancement."""

from __future__ import annotations

from collections.abc import Sequence

from sme_gend.config.settings import Settings
from sme_gend.models.grade_ref import GradeRefRow
from sme_gend.services.grade_label_format import format_grade_display
from sme_gend.services.statutory_limits import canonical_grade

_grades_cache: list[GradeRefRow] | None = None
_grades_settings_key: str | None = None


def _settings_key(settings: Settings) -> str:
    return f"{settings.mysql_host}:{settings.mysql_database}:{settings.mysql_port}"


def load_grade_refs(settings: Settings) -> list[GradeRefRow]:
    global _grades_cache, _grades_settings_key
    key = _settings_key(settings)
    if _grades_cache is not None and _grades_settings_key == key:
        return _grades_cache
    from sme_gend.repositories.grade_repository import GradeRepository

    try:
        _grades_cache = GradeRepository(settings).fetch_all()
    except Exception:
        _grades_cache = []
    _grades_settings_key = key
    return _grades_cache


def invalidate_grade_refs_cache() -> None:
    global _grades_cache, _grades_settings_key
    _grades_cache = None
    _grades_settings_key = None


def grade_abbr(
    raw: str | None,
    *,
    settings: Settings | None = None,
    grades: Sequence[GradeRefRow] | None = None,
) -> str:
    """Code court affiché à l'écran (MDC, ADJ, SLT…)."""
    text = (raw or "").strip()
    if not text:
        return "—"
    refs = list(grades) if grades is not None else (load_grade_refs(settings) if settings else [])
    code = format_grade_display(text, refs)
    if code:
        return code
    fallback = canonical_grade(text)
    return fallback or text


def grade_abbr_optional(code: str | None) -> str:
    return (code or "").strip() or "—"
