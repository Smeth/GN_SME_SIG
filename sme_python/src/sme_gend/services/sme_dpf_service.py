"""Service SME DPF — agrégation depuis MySQL (SoT)."""

from __future__ import annotations

from dataclasses import dataclass, field

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import mois_actif_courant
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.services.personnel_list_order import sort_liste_rows_like_ui
from sme_gend.services.personnel_service import PersonnelService
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.sme_dpf_scope import (
    filter_dpf_rows,
    indisponible_bucket,
    is_dpf_controle_attente,
    is_dpf_controle_malade,
    is_dpf_indisponible,
    is_dpf_present,
    is_dpf_stagiaire,
)


@dataclass(slots=True)
class SmeDpfBundle:
    mois: str
    liste_gnle: list[ListeGeneraleRow] = field(default_factory=list)
    presents: list[ListeGeneraleRow] = field(default_factory=list)
    indisponibles: dict[str, list[ListeGeneraleRow]] = field(default_factory=dict)
    controle_attente: list[ListeGeneraleRow] = field(default_factory=list)
    controle_malades: list[ListeGeneraleRow] = field(default_factory=list)
    stagiaires: list[ListeGeneraleRow] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.liste_gnle)


class SmeDpfService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._personnel = PersonnelService(settings)
        self._referentiel = ReferentielService(settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    def load_bundle(self, *, mois: str | None = None) -> SmeDpfBundle:
        from sme_gend.config.liste_generale_scope import resolve_export_mois, settings_for_chosen_export_mois
        mois_lbl = (mois or resolve_export_mois(self._settings) or "").strip()
        scoped = settings_for_chosen_export_mois(self._settings, mois_lbl)
        personnel = self._personnel if scoped == self._settings else PersonnelService(scoped)
        rows = filter_dpf_rows(personnel.liste_complete())
        grades = self._referentiel.grades()
        rows = sort_liste_rows_like_ui(rows, grades, self._settings)

        presents = [r for r in rows if is_dpf_present(r)]
        indis: dict[str, list[ListeGeneraleRow]] = {}
        for r in rows:
            if not is_dpf_indisponible(r):
                continue
            key = indisponible_bucket(r)
            indis.setdefault(key, []).append(r)

        bundle = SmeDpfBundle(
            mois=mois_lbl,
            liste_gnle=rows,
            presents=presents,
            indisponibles=indis,
            controle_attente=[r for r in rows if is_dpf_controle_attente(r)],
            controle_malades=[r for r in rows if is_dpf_controle_malade(r)],
            stagiaires=[r for r in rows if is_dpf_stagiaire(r)],
        )
        return bundle
