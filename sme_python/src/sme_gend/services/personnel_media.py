"""Chemins des photos personnel et insigne de grade (dossier utilisateur local)."""

from __future__ import annotations

import re
from pathlib import Path

from sme_gend.config.user_paths import resolved_personnel_media_root

_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_GRADE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")


def personnel_media_root() -> Path:
    return resolved_personnel_media_root()


def person_photo_path(personnel_id: str) -> Path | None:
    """Premier fichier trouvé ``photos/{id}.(jpg|png|…)``."""
    pid = (personnel_id or "").strip()
    if not pid:
        return None
    base = personnel_media_root() / "photos"
    for ext in _PHOTO_EXTENSIONS:
        p = base / f"{pid}{ext}"
        if p.is_file():
            return p.resolve()
    return None


def grade_insignia_path(grade_label: str | None) -> Path | None:
    """
    Fichier ``grades/{slug}.(png|jpg|…)`` où slug est dérivé du libellé de grade.
    Ex. ``grades/général_de_division.png``.
    """
    g = (grade_label or "").strip()
    if not g:
        return None
    slug = re.sub(r"[^\w\-]+", "_", g.lower(), flags=re.UNICODE)
    slug = slug.strip("_") or "grade"
    slug = slug[:80]
    base = personnel_media_root() / "grades"
    for ext in _GRADE_EXTENSIONS:
        p = base / f"{slug}{ext}"
        if p.is_file():
            return p.resolve()
    return None


def ensure_personnel_media_dirs() -> None:
    root = personnel_media_root()
    (root / "photos").mkdir(parents=True, exist_ok=True)
    (root / "grades").mkdir(parents=True, exist_ok=True)
