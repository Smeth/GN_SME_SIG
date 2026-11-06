"""Garde-fous import / parcours (migrations, tables optionnelles)."""

from __future__ import annotations

import re

from sme_gend.config.settings import Settings


def ensure_import_schema(settings: Settings) -> None:
    """Applique toutes les migrations nécessaires avant un import bundle."""
    from sme_gend.services.mysql_import_period import ensure_mysql_migrations

    ensure_mysql_migrations(settings)


def is_missing_table_error(exc: BaseException) -> bool:
    err = str(exc).lower()
    return "1146" in err or "doesn't exist" in err or "n'existe pas" in err


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html_line(line: str) -> str:
    return _HTML_TAG_RE.sub("", line or "").replace("→", " → ").strip()
