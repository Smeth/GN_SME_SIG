"""Résultats recherche personnel — effectif actif et hors effectif."""

from __future__ import annotations

from dataclasses import dataclass

from sme_gend.models.liste_generale import ListeGeneraleRow


@dataclass(frozen=True, slots=True)
class SortieSummary:
    """Dernière sortie enregistrée (mouvement_effectif)."""

    categorie: str
    motif: str
    observ: str
    annee_mois: str | None

    def label(self) -> str:
        parts = [p for p in (self.motif, self.observ) if (p or "").strip()]
        text = parts[0] if parts else (self.categorie or "PERTE")
        if self.annee_mois:
            return f"{text} ({self.annee_mois})"
        return text


@dataclass(frozen=True, slots=True)
class HistoricalDossierContext:
    """Contexte affichage dossier hors effectif du mois actif."""

    active_mois: str
    last_carriere_mois: str | None
    sortie: SortieSummary | None

    def banner_text(self) -> str:
        mois = (self.active_mois or "").strip() or "—"
        last = (self.last_carriere_mois or "").strip() or "—"
        sortie = self.sortie.label() if self.sortie else "non renseignée"
        return (
            f"Aucune situation trouvée dans l’effectif du mois consulté ({mois}). "
            f"Dernière situation carrière : {last}. "
            f"Dernière sortie enregistrée : {sortie}. "
            "La position administrative ci-dessous appartient à la situation historique affichée. Consultation seule."
        )


@dataclass(frozen=True, slots=True)
class PersonnelSearchHit:
    """Une ligne de résultat recherche étendue."""

    kind: str  # "active" | "historical"
    row: ListeGeneraleRow
    sortie: SortieSummary | None = None
    last_carriere_mois: str | None = None
    active_mois_label: str | None = None

    @property
    def is_active(self) -> bool:
        return self.kind == "active"

    def status_label(self) -> str:
        if self.is_active:
            ym = (self.active_mois_label or "").strip()
            return f"Effectif {ym}" if ym else "Effectif"
        return "Hors effectif"

    def sortie_label(self) -> str:
        if self.is_active:
            return "—"
        if self.sortie:
            return self.sortie.label()
        return "—"
