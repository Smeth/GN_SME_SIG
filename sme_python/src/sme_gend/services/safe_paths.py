"""Validation des chemins importés et remplacement atomique des fichiers."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import tempfile
import re
import logging
from collections.abc import Callable

from sme_gend.services.file_contention import retry_file_operation, cleanup_owned_temporary


def relative_parts(value: str) -> tuple[str, ...]:
    """Valide aussi la syntaxe Windows lorsque les tests tournent ailleurs."""
    text = str(value).replace("\\", "/")
    windows = PureWindowsPath(text)
    if not text or windows.drive or windows.root or PurePosixPath(text).is_absolute():
        raise ValueError("Chemin relatif attendu.")
    parts = text.rstrip("/").split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.endswith((" ", "."))
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part, re.I)):
            raise ValueError("Nom de fichier ou composant de chemin interdit.")
    return tuple(parts)


def contained_path(root: Path, value: str) -> Path:
    parts = relative_parts(value)
    base = Path(root).resolve()
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base) or target == base:
        raise ValueError("La destination sort du dossier autorisé.")
    return target


def file_key(value: str) -> str:
    key = str(value or "").strip()
    parts = relative_parts(key)
    if len(parts) != 1:
        raise ValueError("L'identifiant doit être un nom de fichier simple.")
    return key


@contextmanager
def atomic_destination(target: Path, *, temporary_suffix: str = ".part", replace_existing: bool = True,
                       before_publish: Callable[[], None] | None = None):
    """Une copie interrompue ne tronque pas le fichier déjà présent."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not re.fullmatch(r"\.[A-Za-z0-9]+", temporary_suffix):
        raise ValueError("Suffixe temporaire invalide.")
    fd, name = tempfile.mkstemp(prefix=".sme-", suffix=temporary_suffix, dir=target.parent)
    os.close(fd)
    temporary = Path(name)
    consumed = False
    primary_error = None
    try:
        yield temporary
        if replace_existing:
            retry_file_operation(lambda:temporary.replace(target),path=target,before_attempt=before_publish)
            consumed = True
        elif os.name == "nt":
            # Keep exclusive publication exclusive, including after a contention.
            retry_file_operation(lambda:temporary.rename(target),path=target,before_attempt=before_publish)
            consumed = True
        else:
            if before_publish is not None:before_publish()
            os.link(temporary, target)
            temporary.unlink()
            consumed = True
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if not consumed:
            try:
                cleanup_owned_temporary(temporary)
            except OSError:
                if primary_error is None:raise
                logging.getLogger(__name__).warning(
                    "Nettoyage du temporaire %s impossible ; erreur initiale conservée.",temporary.name)
                add_note = getattr(primary_error,'add_note',None)
                if callable(add_note):add_note('Une copie temporaire non publiée reste dans le dossier cible.')


def atomic_copy(source: Path, target: Path, *, before_publish: Callable[[], None] | None = None) -> None:
    if Path(source).resolve() == Path(target).resolve():
        return
    with atomic_destination(target,before_publish=before_publish) as temporary:
        shutil.copy2(source, temporary)


def atomic_write(target: Path, data: bytes) -> None:
    with atomic_destination(target) as temporary:
        temporary.write_bytes(data)
