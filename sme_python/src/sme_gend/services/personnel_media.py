"""Chemins des photos personnel et insigne de grade (dossier utilisateur local)."""

from __future__ import annotations

import re
import hashlib
import sys
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from sme_gend.config.user_paths import resolved_personnel_media_root
from sme_gend.services.safe_paths import file_key, contained_path, atomic_copy

_PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
_GRADE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")


def personnel_media_root() -> Path:
    return resolved_personnel_media_root()


def _bundled_media_roots() -> list[Path]:
    """Insignes livrés avec l'exécutable portable (à côté de l'exe ou dans _internal)."""
    if not getattr(sys, "frozen", False):
        return []
    candidates: list[Path] = []
    try:
        from sme_gend.config.settings import project_root

        candidates.append(project_root() / "personnel_media")
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "personnel_media")
    out: list[Path] = []
    seen: set[str] = set()
    for root in candidates:
        if not root.is_dir():
            continue
        key = str(root.resolve()).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(root.resolve())
    return out


def _project_data_media_root() -> Path | None:
    """Insignes livrés en dev (``data/personnel_media``) si le profil utilisateur est vide."""
    if getattr(__import__("sys"), "frozen", False):
        return None
    try:
        from sme_gend.config.settings import project_root

        root = project_root() / "data" / "personnel_media"
        if (root / "grades").is_dir() and any((root / "grades").iterdir()):
            return root.resolve()
    except Exception:
        pass
    return None


def _media_search_roots() -> list[Path]:
    """Racines à parcourir pour lire photos / insignes (profil utilisateur puis repli portable)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(root: Path | None) -> None:
        if root is None or not root.is_dir():
            return
        key = str(root.resolve()).casefold()
        if key in seen:
            return
        seen.add(key)
        roots.append(root.resolve())

    _add(personnel_media_root())
    for bundled in _bundled_media_roots():
        _add(bundled)
    _add(_project_data_media_root())
    return roots


def grade_slug(grade_label: str | None) -> str:
    g = (grade_label or "").strip()
    if not g:
        return "grade"
    slug = re.sub(r"[^\w\-]+", "_", g.lower(), flags=re.UNICODE)
    slug = slug.strip("_") or "grade"
    return slug[:80]


def person_photo_path(personnel_id: str) -> Path | None:
    """Premier fichier trouvé ``photos/{id}.(jpg|png|…)``."""
    try:
        pid = file_key(personnel_id)
    except ValueError:
        return None
    for root in _media_search_roots():
        for ext in _PHOTO_EXTENSIONS:
            try:
                p = contained_path(root, f"photos/{pid}{ext}")
            except ValueError:
                continue
            if p.is_file():
                return p.resolve()
    return None


def _ascii_fold(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _insignia_slug_variants(label: str) -> list[str]:
    slug = grade_slug(label)
    variants: list[str] = []
    for candidate in (slug, _ascii_fold(slug)):
        if candidate and candidate not in variants:
            variants.append(candidate)
        alt = candidate.replace("_", "-")
        if alt != candidate and alt not in variants:
            variants.append(alt)
    return variants


def _grade_insignia_lookup_labels(
    grade_raw: str | None,
    grades: Sequence[object] | None = None,
) -> list[str]:
    """Libellés à tester : code agent, libellé référentiel, alias croisés."""
    g = (grade_raw or "").strip()
    if not g:
        return []
    labels: list[str] = [g]
    cf = g.casefold()
    for gr in grades or ():
        code = str(getattr(gr, "code", "") or "").strip()
        lib = str(getattr(gr, "libelle", "") or "").strip()
        if code and code.casefold() == cf:
            if lib:
                labels.append(lib)
        elif lib and lib.casefold() == cf:
            if code:
                labels.append(code)
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        key = label.casefold()
        if key not in seen:
            seen.add(key)
            out.append(label)
    return out


def grade_insignia_path(
    grade_label: str | None,
    grades: Sequence[object] | None = None,
) -> Path | None:
    """
    Fichier ``grades/{slug}.(png|jpg|…)`` où slug est dérivé du libellé de grade.
    Résout aussi un code référentiel (ex. LTN → lieutenant.png).
    """
    from sme_gend.services.grade_media import media_index
    labels = _grade_insignia_lookup_labels(grade_label, grades)
    index = media_index()
    for label in labels:
        entry = index.get(label.casefold())
        if entry is None:
            entry = next((v for v in index.values() if str(v.get('label', '')).casefold() == label.casefold()), None)
        if entry and entry.get('file'):
            for root in _media_search_roots():
                path = contained_path(root, entry['file'])
                if path.is_file():
                    return path
    for label in labels:
        for variant in _insignia_slug_variants(label):
            for root in _media_search_roots():
                base = root / "grades"
                for ext in _GRADE_EXTENSIONS:
                    p = base / f"{variant}{ext}"
                    if p.is_file():
                        return p.resolve()
    return None


def ensure_personnel_media_dirs() -> None:
    root = personnel_media_root()
    (root / "photos").mkdir(parents=True, exist_ok=True)
    (root / "grades").mkdir(parents=True, exist_ok=True)


def _normalize_ext(path: Path, allowed: tuple[str, ...], default: str) -> str:
    ext = path.suffix.lower()
    return ext if ext in allowed else default


class MediaWriteCommittedError(RuntimeError):
    """The image is already copied; retry the notification, not the file write."""
    committed = True


def _notify_shared_media_write(root: Path, *, settings=None, shared=None) -> None:
    if not (str(root).startswith(("\\\\", "//")) if shared is None else shared):
        return
    from sme_gend.config import get_settings
    from sme_gend.repositories.mysql_connection import current_transaction_settings
    from sme_gend.services.data_sync import publish_data_change
    try:
        publish_data_change(settings or current_transaction_settings() or get_settings(), reason="personnel_media")
    except Exception as exc:
        raise MediaWriteCommittedError(
            "Image déjà copiée dans le dossier partagé, mais notification des postes impossible. "
            "Ne recommencez pas la copie ; vérifiez MySQL et actualisez les autres postes."
        ) from exc


def media_variant_lock(root: Path, folder: str, key: str):
    """All application writers use one persistent lock per image identity."""
    from sme_gend.services.media_storage_coordination import locked_media_stream
    lock_id = hashlib.sha256(file_key(key).casefold().encode('utf-8')).hexdigest()
    return locked_media_stream(contained_path(root, f".media-locks/{folder}-{lock_id}.lock"))


def _save_media(key: str, source: Path, folder: str, allowed: tuple[str, ...], default: str) -> Path:
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {src}")
    from sme_gend.services.shared_media import shared_media_write_target
    copied = False
    try:
        with shared_media_write_target(personnel_media_root()) as target:
            root = target.root
            ext = _normalize_ext(src, allowed, default)
            dest = contained_path(root, f"{folder}/{key}{ext}")
            with media_variant_lock(root, folder, key):
                target.check()
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_copy(src, dest, before_publish=target.check)
                copied = True
                for sibling in dest.parent.iterdir():
                    if (sibling.is_file() and sibling.stem.casefold() == key.casefold()
                            and sibling.suffix.lower() in allowed and sibling.resolve() != dest.resolve()):
                        try:
                            from sme_gend.services.file_contention import retry_file_operation
                            retry_file_operation(lambda:sibling.unlink(),path=sibling,before_attempt=target.check)
                        except OSError as exc:
                            raise MediaWriteCommittedError(
                                "Image copiée, mais une ancienne variante reste inaccessible. "
                                "Vérifiez les droits du dossier partagé avant de recommencer."
                            ) from exc
            target.check()
            _notify_shared_media_write(root, settings=target.settings, shared=target.shared)
        return dest
    except MediaWriteCommittedError:
        raise
    except Exception as exc:
        if copied:
            raise MediaWriteCommittedError(
                "Image déjà copiée, mais coordination des postes interrompue. "
                "Ne recommencez pas la copie ; vérifiez MySQL et actualisez les postes."
            ) from exc
        raise


def save_person_photo(personnel_key: str, source: Path) -> Path:
    """Copie atomique ; sérialise aussi le nettoyage des variantes entre postes."""
    return _save_media(file_key(personnel_key), source, "photos", _PHOTO_EXTENSIONS, ".jpg")


def save_grade_insignia(grade_label: str, source: Path) -> Path:
    """Copie une image vers grades/{slug}.ext, sous le même verrou partagé."""
    return _save_media(file_key(grade_slug(grade_label)), source, "grades", _GRADE_EXTENSIONS, ".png")
