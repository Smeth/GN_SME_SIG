"""Lecture du cycle mensuel, sans calculer les effectifs du tableau de bord."""
from dataclasses import dataclass
from sme_gend.repositories.sme_repository import SmeRepository
from sme_gend.services.sme_workspace_service import SmeWorkspaceService


@dataclass(frozen=True)
class MonthCycleSnapshot:
    sme_rows: tuple
    active_month: str
    sme_table_ok: bool = True
    document_mode: str | None = None


def load_month_cycle(settings):
    repository = SmeRepository(settings)
    if not repository.table_exists():
        raise RuntimeError("Les périodes ne sont pas disponibles. Vérifiez la connexion et l’initialisation des données.")
    rows = tuple(repository.fetch_all())
    active = SmeWorkspaceService(settings).load_active_month_snapshot().active_month
    from sme_gend.services.document_month_cycle import get_cycle_mode
    return MonthCycleSnapshot(rows, active or "", document_mode=get_cycle_mode(settings))
