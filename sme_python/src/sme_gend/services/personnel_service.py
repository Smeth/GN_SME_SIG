"""Règles métier — liste générale (couche au-dessus des repositories)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import mois_actif_courant
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.grade_repository import GradeRepository
from sme_gend.repositories.liste_generale_repository import ListeGeneraleRepository
from sme_gend.services import audit_log
from sme_gend.services.personnel_list_order import sort_liste_generale_default_order


class PersonnelService:
    """Service principal : liste du personnel (lecture et mise à jour)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        """Recrée les dépôts après changement de chemins (sans redémarrage)."""
        self._settings = settings
        self._repo = ListeGeneraleRepository(settings)
        self._grades = GradeRepository(settings)

    def liste_complete(self) -> list[ListeGeneraleRow]:
        raw = self._repo.fetch_all()
        grades = self._grades.fetch_all()
        return sort_liste_generale_default_order(raw, grades)

    def liste_presents(self) -> list[ListeGeneraleRow]:
        """Personnes comptées présentes : Padmin « présent » ou stage interne (liste des présents — base)."""
        return self._repo.fetch_presents()

    def get_by_id(self, row_id: str) -> ListeGeneraleRow | None:
        return self._repo.fetch_by_id(row_id)

    def update_personnel(self, before: ListeGeneraleRow, after: ListeGeneraleRow) -> None:
        """Met à jour Access et enregistre la traçabilité (fichier JSON Lines)."""
        if before.id != after.id:
            raise ValueError("L'identifiant du personnel ne peut pas être modifié.")
        changes = audit_log.diff_personnel_rows(before, after)
        if not changes:
            return
        self._repo.update_row(after)
        ctx = (mois_actif_courant() or "").strip() or None
        audit_log.append_personnel_audit(
            action="update",
            personnel_id=after.id,
            changes=changes,
            snapshot_before=audit_log.row_to_audit_dict(before),
            snapshot_after=audit_log.row_to_audit_dict(after),
            sme_mois_context=ctx,
        )
