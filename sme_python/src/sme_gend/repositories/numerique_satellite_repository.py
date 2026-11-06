"""Lecture tables satellites situation numérique : MySQL."""

from __future__ import annotations

from sme_gend.repositories.monthly_write_guard import UNSET

from sme_gend.config.settings import Settings
from sme_gend.models.numerique_satellite import (
    DeserteurDetail,
    MaladeDetail,
    MissionDetail,
    StageDetail,
)
__all__ = [
    "NumeriqueSatelliteRepository",
    "MissionDetail",
    "MaladeDetail",
    "DeserteurDetail",
    "StageDetail",
]


class NumeriqueSatelliteRepository:
    """Données satellites jointes sur Id Access (``ListeGenerale.Id`` / ``id_acces``)."""

    def __init__(self, settings: Settings) -> None:
        from sme_gend.repositories.mysql_numerique_repository import (
            MySQLNumeriqueRepository,
        )

        self._impl = MySQLNumeriqueRepository(settings)

    def load_missions(self, *, legacy_id: str | None = None) -> dict[str, MissionDetail]:
        return self._impl.load_missions(**({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_malades(self, *, legacy_id: str | None = None) -> dict[str, MaladeDetail]:
        return self._impl.load_malades(**({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_deserteurs(self) -> dict[str, DeserteurDetail]:
        return self._impl.load_deserteurs()

    def load_latest_conges_for_period(self):
        return self._impl.load_latest_conges_for_period()

    def load_latest_conges_by_type_for_period(self):
        return self._impl.load_latest_conges_for_period(by_type=True)

    def load_position_events_for_period(self):
        return self._impl.load_position_events_for_period()

    def load_stages_loc(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._impl.load_stages_loc(**({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_stages_ex(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._impl.load_stages_ex(**({} if legacy_id is None else {'legacy_id': legacy_id}))

    def load_stages_internal(self, *, legacy_id: str | None = None) -> dict[str, StageDetail]:
        return self._impl.load_stages_internal(**({} if legacy_id is None else {'legacy_id': legacy_id}))

    def upsert_malade(self, person_id: str, detail: MaladeDetail, *, expected=UNSET) -> None:
        return self._impl.upsert_malade(person_id, detail, **({} if expected is UNSET else {"expected": expected}))

    def upsert_mission(self, person_id: str, detail: MissionDetail, *, expected=UNSET) -> None:
        return self._impl.upsert_mission(person_id, detail, **({} if expected is UNSET else {"expected": expected}))

    def upsert_stage(
        self,
        person_id: str,
        detail: StageDetail,
        *,
        type_code: str,
        date_debut: str | None = None,
        date_fin: str | None = None,
        expected=UNSET,
    ) -> None:
        return self._impl.upsert_stage(
            person_id,
            detail,
            type_code=type_code,
            date_debut=date_debut,
            date_fin=date_fin,
            **({} if expected is UNSET else {"expected": expected}),
        )

    def clear_stage_for_period(self, person_id: str, type_code: str) -> None:
        return self._impl.clear_stage_for_period(person_id, type_code)

    def stage_history_snapshot(self, person_id: str):
        return self._impl.list_stages_for_agent(person_id)

    def clear_other_stages_for_period(self, person_id: str, *, keep_type: str, expected=UNSET) -> None:
        return self._impl.clear_other_stages_for_period(
            person_id, keep_type=keep_type, **({} if expected is UNSET else {"expected": expected}),
        )

    def delete_malade(self, person_id: str, *, expected=UNSET) -> None:
        return self._impl.delete_malade(person_id, **({} if expected is UNSET else {"expected": expected}))

    def upsert_desertion(self, person_id: str, detail: DeserteurDetail, *, expected=UNSET) -> None:
        return self._impl.upsert_desertion(person_id, detail, **({} if expected is UNSET else {"expected": expected}))

    def delete_desertion(self, person_id: str, *, expected=UNSET) -> None:
        return self._impl.delete_desertion(person_id, **({} if expected is UNSET else {"expected": expected}))
