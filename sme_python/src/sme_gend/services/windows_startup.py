"""Lancement automatique au démarrage de Windows (registre HKCU Run)."""

from __future__ import annotations

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_VALUE_NAME = "SGI_DPF"


def is_supported() -> bool:
    return sys.platform == "win32"


def startup_command() -> str:
    """Commande enregistrée dans le registre pour relancer l'application."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    python = Path(sys.executable).resolve()
    return f'"{python}" -m sme_gend'


def is_startup_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_VALUE_NAME)
            return bool(str(value or "").strip())
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> None:
    if not is_supported():
        raise OSError("Le lancement au démarrage n'est disponible que sous Windows.")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
            return
        try:
            winreg.DeleteValue(key, APP_VALUE_NAME)
        except FileNotFoundError:
            pass


def apply_startup_preference(enabled: bool) -> None:
    """Active ou désactive l'entrée registre selon la préférence utilisateur."""
    set_startup_enabled(enabled)
