"""Tests Phase D — provenance dossier."""

from __future__ import annotations

from sme_gend.services.dossier_provenance import (
    OBS_IMPORT_PREFIX,
    OBS_SAISIE_APPLICATIVE,
    dossier_entry_provenance,
)


def test_provenance_import_auto() -> None:
    assert dossier_entry_provenance(f"{OBS_IMPORT_PREFIX} 2026-05") == "Import auto"


def test_provenance_saisie_manuelle() -> None:
    assert dossier_entry_provenance(OBS_SAISIE_APPLICATIVE) == "Saisie manuelle"


def test_provenance_vide() -> None:
    assert dossier_entry_provenance(None) == "—"
    assert dossier_entry_provenance("  ") == "—"
