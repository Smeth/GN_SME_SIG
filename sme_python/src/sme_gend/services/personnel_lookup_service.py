"""Recherche personnel — effectif actif et hors effectif (décès, sorties)."""

from __future__ import annotations

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import load_sme_context
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.personnel_lookup import (
    HistoricalDossierContext,
    PersonnelSearchHit,
    SortieSummary,
)
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.repositories.mysql_connection import MySQLConnection
from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm


def _dedupe_search_hits(hits: list[PersonnelSearchHit]) -> list[PersonnelSearchHit]:
    """Une ligne par agent (priorité à l'effectif courant)."""
    order: list[str] = []
    by_key: dict[str, PersonnelSearchHit] = {}
    for h in hits:
        key = (h.row.id or h.row.satellite_join_key() or "").strip()
        if not key:
            continue
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = h
            order.append(key)
        elif h.kind == "active" and prev.kind != "active":
            by_key[key] = h
    return [by_key[k] for k in order]


def _needle_match(needle: str, *parts: str | None) -> bool:
    n = (needle or "").strip().upper()
    if len(n) < 2:
        return False
    hay = " ".join(p for p in parts if p).upper()
    return n in hay


class PersonnelLookupService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._agents = MySQLAgentRepository(settings)

    def active_mois(self) -> str | None:
        return canonical_sme_mois_yyyy_mm(load_sme_context().mois_actif or "")

    def search_extended(self, term: str) -> list[PersonnelSearchHit]:
        """Effectif courant + agents hors effectif dont le nom/matricule correspond."""
        needle = (term or "").strip()
        if len(needle) < 2:
            return []

        ym = self.active_mois()
        hits: list[PersonnelSearchHit] = []
        for row in self._agents.search_current_personnel(needle):
            hits.append(
                PersonnelSearchHit(
                    kind="active",
                    row=row,
                    active_mois_label=ym,
                )
            )

        for agent_id, row, last_mois, sortie_row in self._search_historical_agents(needle):
            sortie = (
                SortieSummary(
                    categorie=str(sortie_row[0] or "PERTE"),
                    motif=str(sortie_row[1] or "").strip(),
                    observ=str(sortie_row[2] or "").strip(),
                    annee_mois=str(sortie_row[3]).strip() if sortie_row[3] else None,
                )
                if sortie_row
                else None
            )
            hits.append(
                PersonnelSearchHit(
                    kind="historical",
                    row=row,
                    sortie=sortie,
                    last_carriere_mois=last_mois,
                    active_mois_label=ym,
                )
            )

        hits.sort(key=lambda h: ((0 if h.is_active else 1), (h.row.noms or "").upper()))
        return _dedupe_search_hits(hits)

    def search_active_effectif(self, term: str) -> list[PersonnelSearchHit]:
        """Effectif courant uniquement (module stages / ajout depuis l'effectif)."""
        needle = (term or "").strip()
        if len(needle) < 2:
            return []
        ym = self.active_mois()
        hits: list[PersonnelSearchHit] = []
        for row in self._agents.search_current_personnel(needle):
            hits.append(
                PersonnelSearchHit(
                    kind="active",
                    row=row,
                    active_mois_label=ym,
                )
            )
        hits.sort(key=lambda h: (h.row.noms or "").upper())
        return _dedupe_search_hits(hits)

    def _search_historical_agents(
        self,
        needle: str,
    ) -> list[tuple[int, ListeGeneraleRow, str | None, tuple | None]]:
        return self._agents.search_historical_personnel(needle)

    def fetch_sortie_summary(self, agent_id: int) -> SortieSummary | None:
        mc = MySQLConnection(self._settings)
        with mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(m.categorie_mouvement, m.type_mouvement),
                           COALESCE(m.motif, ''), COALESCE(m.observ, ''),
                           p.annee_mois
                    FROM mouvement_effectif m
                    LEFT JOIN sme_periode p ON p.id = m.sme_periode_id
                    WHERE m.agent_id = %s
                      AND UPPER(COALESCE(m.categorie_mouvement, m.type_mouvement, '')) = 'PERTE'
                    ORDER BY COALESCE(p.annee_mois, '0000-00') DESC, m.id DESC
                    LIMIT 1
                    """,
                    (int(agent_id),),
                )
                row = cur.fetchone()
        if not row:
            return None
        return SortieSummary(
            categorie=str(row[0] or "PERTE"),
            motif=str(row[1] or "").strip(),
            observ=str(row[2] or "").strip(),
            annee_mois=str(row[3]).strip() if row[3] else None,
        )

    def build_historical_context(self, agent_id: int) -> HistoricalDossierContext:
        return HistoricalDossierContext(
            active_mois=self.active_mois() or "—",
            last_carriere_mois=self._agents.fetch_latest_carriere_mois(agent_id),
            sortie=self.fetch_sortie_summary(agent_id),
        )

    def dossier_row_for_legacy_id(self, legacy_access_id: str) -> tuple[ListeGeneraleRow, HistoricalDossierContext] | None:
        leg = (legacy_access_id or "").strip()
        if not leg:
            return None
        mc = MySQLConnection(self._settings)
        with mc.connect(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM agent WHERE legacy_access_id = %s UNION ALL SELECT canonical_agent_id FROM agent_identity_alias WHERE alias_legacy_id=%s LIMIT 1", (leg, leg))
                row = cur.fetchone()
        if not row:
            return None
        aid = int(row[0])
        liste_row = self._agents.fetch_latest_liste_row_for_agent(aid)
        if liste_row is None:
            return None
        return liste_row, self.build_historical_context(aid)

    def resolve_dossier_for_legacy_id(self, legacy_access_id: str):
        """Fiche du mois consulté, sinon historique après absence vérifiée."""
        from sme_gend.config.liste_generale_scope import resolve_liste_view_mois
        from dataclasses import replace

        leg = (legacy_access_id or "").strip()
        if not leg:
            return None
        row = self._agents.fetch_scoped_by_legacy(leg)
        if row is not None:
            return row, None
        pack = self.dossier_row_for_legacy_id(leg)
        if pack is None:
            return None
        row, context = pack
        return row, replace(context, active_mois=resolve_liste_view_mois(self._settings) or "—")
