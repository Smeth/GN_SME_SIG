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

    def connect(self, *, login_timeout: int = 8, query_timeout: int = 120) -> pyodbc.Connection:
        """
        Ouvre la connexion ODBC.

        ``login_timeout`` borne l'établissement de la connexion (secondes) ;
        ``query_timeout`` borne chaque requête exécutée sur la connexion —
        sans cette borne, un pilote Access qui se bloque (fichier à compacter,
        table volumineuse, verrou) fait attendre indéfiniment sans jamais
        remonter d'erreur (constaté en usage réel : blocage de plus d'une heure).
        """
        from sme_gend.services.access_odbc import build_access_connection_string

        cs, _driver = build_access_connection_string(
            self._settings.database_path,
            preferred_driver=self._settings.odbc_driver,
        )
        conn = pyodbc.connect(cs, autocommit=True, timeout=login_timeout)
        try:
            conn.timeout = query_timeout
        except Exception:
            pass  # pilote ne supportant pas SQL_ATTR_QUERY_TIMEOUT : pas de borne, tant pis
        return conn
