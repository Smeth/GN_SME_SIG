"""Avancement actif : dossiers agents, critères calculables et barème partagé."""

from __future__ import annotations

from datetime import date

from sme_gend.config.settings import Settings
from sme_gend.models.avancement import (
    AgentAvancementInput,
    AvancementRegle,
    VigilanceRow,
    default_rules,
)
from sme_gend.repositories.mysql_avancement_repository import MySQLAvancementRepository
from sme_gend.repositories.avancement_rule_guard import bind_rule
from sme_gend.services.avancement_engine import evaluer_agent


class AvancementRules(dict[str, AvancementRegle]):
    """Visible defaults/overrides plus the exact persisted collection version."""
    def __init__(self, values, snapshot):
        super().__init__(values)
        self.snapshot = snapshot


class AvancementService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLAvancementRepository(settings)

    def rebind_settings(self, settings: Settings) -> None:
        self._settings = settings
        self._repo = MySQLAvancementRepository(settings)

    def regles(self) -> AvancementRules:
        try:
            snapshot = self._repo.load_regles_snapshot()
        except Exception as exc:
            raise RuntimeError("Impossible de charger le barème partagé. Vérifiez MySQL et actualisez ; "
                "aucune règle par défaut n'a été substituée.") from exc
        base = {rule.grade_cible: bind_rule(self._settings, rule, None) for rule in default_rules()}
        base.update({rule.grade_cible: rule for rule in snapshot.rows})
        return AvancementRules(base, snapshot)

    def save_regle(self, regle: AvancementRegle, *, expected: AvancementRegle | None = None) -> None:
        self._repo.save_regle(regle, expected=expected)

    def reset_regles(self, *, expected=None) -> None:
        self._repo.reset_regles(expected=expected)

    def vigilance(
        self,
        *,
        annee: int,
        trimestre: int | None = None,
        on: date | None = None,
        horizon_days: int = 365,
    ) -> list[VigilanceRow]:
        from sme_gend.services.avancement_reference_date import avancement_reference_date
        on = avancement_reference_date(self._settings, on=on)
        agents = self._repo.load_agents(on=on)
        book = self.regles()
        # annee/trimestre remain accepted for existing callers; retired
        # campaign tables no longer supply any input to this calculation.
        rows: list[VigilanceRow] = []
        for agent in agents:
            cibles = _cibles(agent)
            if not cibles:
                rows.extend(evaluer_agent(agent, book, on=on, horizon_days=horizon_days, cibles=[]))
                continue
            for cible in cibles:
                rows.extend(
                    evaluer_agent(
                        agent,
                        book,
                        on=on,
                        horizon_days=horizon_days,
                        cibles=[cible],
                    )
                )
        return rows


def _cibles(agent: AgentAvancementInput) -> list[str]:
    from sme_gend.services.avancement_engine import cibles_possibles

    return cibles_possibles(agent.grade_raw)
