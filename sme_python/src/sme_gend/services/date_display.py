"""Affichage des dates au format français (jj/mm/aaaa) — couche présentation.

Les modèles et les requêtes gardent le format ISO (``YYYY-MM-DD``) pour le tri et
le stockage ; seule la fiche personnel affichait de l'ISO à l'écran, ce qui gêne
la lecture et la comparaison avec les états (liste, PDF, exports) en jj/mm/aaaa.
"""

from __future__ import annotations

from datetime import date, datetime

FR_DATE_FMT = "%d/%m/%Y"
EMPTY_MARK = "—"


def fr_date(value: object, *, empty: str = EMPTY_MARK) -> str:
    """Une date (``date``, ``datetime``, chaîne ISO ou déjà formatée) en jj/mm/aaaa.

    * ``None`` / vide → ``empty`` (tiret demi-cadratin par défaut, comme les tableaux).
    * chaîne non ISO (ex. déjà ``12/03/1985`` ou ``—``) → retournée telle quelle.
    """
    if value is None:
        return empty
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return empty
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            try:
                return datetime.strptime(s[:10], "%Y-%m-%d").strftime(FR_DATE_FMT)
            except ValueError:
                return s
        return s
    if isinstance(value, (datetime, date)):
        try:
            return value.strftime(FR_DATE_FMT)
        except ValueError:  # année < 1000 : strftime refusé sur certaines plateformes
            iso = value.isoformat()
            return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"
    s = str(value).strip()
    return s or empty
