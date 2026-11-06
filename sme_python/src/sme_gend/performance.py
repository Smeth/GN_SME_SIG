"""Profil performance / faible mémoire (PC 2–4 Go RAM)."""

from __future__ import annotations

import logging
import os
import sys


def low_memory_mode() -> bool:
    """Toujours vrai : les vues lourdes restent en chargement différé (LazyStackPage)."""
    return True


def default_log_level() -> int:
    if os.environ.get("SME_LOG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        return logging.DEBUG
    return logging.INFO


def apply_runtime_profile(app=None) -> None:
    """Réglages processus/Qt pour limiter RAM et I/O."""
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    if app is not None:
        from PySide6.QtCore import Qt

        app.setAttribute(Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents, True)
