"""Identifiant stable du poste (fichier local) pour les sessions applicatives."""

from __future__ import annotations

import json
import secrets
import hashlib
from pathlib import Path

from sme_gend.config.user_paths import user_application_data_dir

_FILENAME = "client_instance.json"


def _machine_identity() -> str:
    """Bind a copied profile identifier to the actual computer."""
    import platform
    import sys

    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r'SOFTWARE\Microsoft\Cryptography', 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                value, _ = winreg.QueryValueEx(key, 'MachineGuid')
                if value:
                    return str(value).strip().casefold()
        except OSError:
            pass
    return platform.node().strip().casefold()


def _bound_id(local_id: str) -> str:
    return hashlib.sha256((local_id + '\0' + _machine_identity()).encode('utf-8')).hexdigest()


def client_instance_id() -> str:
    path = user_application_data_dir() / _FILENAME
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            cid = str(raw.get("id", "")).strip()
            if cid:
                return _bound_id(cid)
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    cid = secrets.token_hex(16)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"id": cid}, indent=2), encoding="utf-8")
    return _bound_id(cid)


def machine_label() -> str:
    import platform
    import socket

    host = (socket.gethostname() or "").strip()
    user = ""
    try:
        user = (__import__("getpass").getuser() or "").strip()
    except Exception:
        pass
    sys_name = platform.system()
    parts = [p for p in (host, user, sys_name) if p]
    return " · ".join(parts) if parts else "Poste inconnu"
