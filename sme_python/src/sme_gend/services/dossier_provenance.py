"""Origine des entrées dossier agent (import vs saisie manuelle)."""

from __future__ import annotations

OBS_SAISIE_APPLICATIVE = "Saisie applicative"
OBS_IMPORT_PREFIX = "Import archive"


def dossier_entry_provenance(obs: str | None) -> str:
    """Libellé court pour badge tableau dossier."""
    t = (obs or "").strip()
    if not t:
        return "—"
    if t.startswith(OBS_IMPORT_PREFIX):
        return "Import auto"
    if t == OBS_SAISIE_APPLICATIVE:
        return "Saisie manuelle"
    return "Manuel"


def dossier_entry_provenance_tooltip(obs: str | None) -> str:
    t = (obs or "").strip()
    return t if t else "Origine non renseignée"
