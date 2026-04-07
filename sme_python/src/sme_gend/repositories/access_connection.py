"""Gestion de connexion ODBC Access (context manager)."""

from __future__ import annotations

import pyodbc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sme_gend.config.settings import Settings


class AccessConnection:
    """Ouvre une connexion ODBC vers un fichier .mdb / .accdb."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def connect(self) -> pyodbc.Connection:
        conn = pyodbc.connect(self._settings.connection_string(), autocommit=True)
        return conn
