"""Ouverture dossier personnel hors effectif actif."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import HistoricalDossierContext, PersonnelSearchHit
from sme_gend.services.personnel_lookup_service import PersonnelLookupService
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.ui.personnel_dossier_open import open_personnel_dossier_dialog


def open_historical_dossier_dialog(
    parent: QWidget | None,
    settings: Settings,
    *,
    row: ListeGeneraleRow,
    context: HistoricalDossierContext,
) -> None:
    ref = ReferentielService(settings)
    svc = PersonnelService(settings)
    open_personnel_dossier_dialog(
        parent,
        row,
        ref,
        svc,
        allow_edit=False,
        historical_context=context,
        refresh_from_db=False,
    )


def open_historical_dossier_for_legacy(
    parent: QWidget | None,
    settings: Settings,
    legacy_access_id: str,
) -> bool:
    lookup = PersonnelLookupService(settings)
    pack = lookup.resolve_dossier_for_legacy_id(legacy_access_id)
    if pack is None:
        QMessageBox.information(
            parent,
            "Dossier",
            "Aucune fiche trouvée pour cet identifiant.",
        )
        return False
    row, ctx = pack
    if ctx is None:
        open_personnel_dossier_dialog(
            parent, row, ReferentielService(settings), PersonnelService(settings),
            allow_edit=False, refresh_from_db=False,
        )
    else:
        open_historical_dossier_dialog(parent, settings, row=row, context=ctx)
    return True


def open_dossier_from_search_hit(
    parent: QWidget | None,
    settings: Settings,
    hit: PersonnelSearchHit,
    *,
    navigate_active_fn,
) -> None:
    if hit.is_active:
        navigate_active_fn(hit.row.noms or "")
        return
    ctx = HistoricalDossierContext(
        active_mois=hit.active_mois_label or "—",
        last_carriere_mois=hit.last_carriere_mois,
        sortie=hit.sortie,
    )
    open_historical_dossier_dialog(parent, settings, row=hit.row, context=ctx)
