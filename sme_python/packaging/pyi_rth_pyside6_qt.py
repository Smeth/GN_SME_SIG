"""Runtime hook PyInstaller : chemins Qt/PySide6 en mode onedir (Windows)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _configure_qt_paths() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = Path(sys.executable).resolve().parent
    internal = base / "_internal"
    pyside6 = internal / "PySide6"
    plugins = pyside6 / "plugins"
    platforms = plugins / "platforms"
    if plugins.is_dir():
        os.environ["QT_PLUGIN_PATH"] = str(plugins)
    if platforms.is_dir():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms)
    path_parts = [str(pyside6), str(internal)]
    if sys.platform == "win32":
        path_parts.append(str(pyside6 / "Qt" / "bin"))
    os.environ["PATH"] = os.pathsep.join(path_parts + [os.environ.get("PATH", "")])
    if hasattr(os, "add_dll_directory"):
        for folder in (internal, pyside6, pyside6 / "Qt" / "bin"):
            if folder.is_dir():
                try:
                    os.add_dll_directory(str(folder))
                except OSError:
                    pass


_configure_qt_paths()
