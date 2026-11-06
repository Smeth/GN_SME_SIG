"""Résumé lecture seule — stage, mission, détachement, maladie (synthèse dossier)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

_LOG = logging.getLogger(__name__)

from sme_gend.config.settings import Settings
from sme_gend.models.agent_evenement import AgentMissionRow, AgentStageRow
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.models.numerique_satellite import MaladeDetail, MissionDetail, StageDetail
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
from sme_gend.services.detaches_preview_service import _build_sec_lookup
from sme_gend.services.detaches_service import (
    designation_bloc_excel,
    designation_detachement,
    in_detaches_scope,
    sigle_ou_subdivision,
)
from sme_gend.services.detaches_config_service import DetachesConfigService
from sme_gend.services.padmin_satellite import SatelliteKind, satellite_kind


@dataclass(frozen=True, slots=True)
class SituationEnCours:
    """Bloc synthèse dossier — champs affichés en lecture seule."""

    fields: list[tuple[str, str]]
    missing_satellite_detail: bool = False


_SECTION_TITLE = "SITUATION EN COURS"

_STAGE_TYPE_LABEL = {
    "LOCAL": "Stage local",
    "EXTERIEUR": "Stage extérieur",
    "INTERNE": "Stage interne",
}

_KIND_LABEL: dict[SatelliteKind, str] = {
    "malade": "Malade",
    "mission": "Mission",
    "stage_local": "Stage local",
    "stage_ex": "Stage extérieur",
    "stage_interne": "Stage interne",
}

_STAGE_KIND_TO_CODE: dict[str, str] = {
    "stage_local": "LOCAL",
    "stage_ex": "EXTERIEUR",
    "stage_interne": "INTERNE",
}


def _disp(val: str | None) -> str:
    s = (val or "").strip()
    return s if s else "—"


def _stage_loader(sat: NumeriqueSatelliteRepository, kind: SatelliteKind):
    if kind == "stage_local":
        return sat.load_stages_loc
    if kind == "stage_ex":
        return sat.load_stages_ex
    return sat.load_stages_internal


def _stage_from_dossier(
    rows: list[AgentStageRow],
    kind: SatelliteKind,
) -> AgentStageRow | None:
    code = _STAGE_KIND_TO_CODE.get(kind, "")
    for s in rows:
        if (s.type_stage or "").upper() == code:
            return s
    return rows[0] if rows else None


def _mission_from_dossier(rows: list[AgentMissionRow]) -> AgentMissionRow | None:
    return rows[0] if rows else None


def _has_stage_detail(d: StageDetail | None) -> bool:
    if d is None:
        return False
    return any(
        (d.nature_stage or "").strip()
        or (d.lieu_stage or "").strip()
        or (d.date_debut_fin or "").strip()
    )


def _has_mission_detail(d: MissionDetail | None) -> bool:
    if d is None:
        return False
    return any(
        (d.nature or "").strip()
        or (d.lieu_mission or "").strip()
        or (d.refnds or "").strip()
        or (d.date_depart or "").strip()
    )


def _has_malade_detail(d: MaladeDetail | None) -> bool:
    if d is None:
        return False
    return any(
        (d.pathologie or "").strip()
        or (d.date_constat or "").strip()
        or (d.obs or "").strip()
    )


def build_situation_en_cours(
    row: ListeGeneraleRow,
    settings: Settings,
) -> SituationEnCours | None:
    """
    Champs pour la carte « Situation en cours » (dossier → Synthèse).

    Priorité : position satellite (mission, stage, malade) puis périmètre détaché.
    """
    kind = satellite_kind(row.position_admin)
    if kind is not None:
        return _build_satellite_situation(row, settings, kind)
    if in_detaches_scope(row):
        return _build_detache_situation(row, settings)
    return None


def section_title() -> str:
    return _SECTION_TITLE


def _safe_sat_repo(settings: Settings) -> NumeriqueSatelliteRepository | None:
    try:
        return NumeriqueSatelliteRepository(settings)
    except Exception:
        _LOG.debug("Satellites indisponibles pour la synthèse dossier", exc_info=True)
        return None


def _safe_events(settings: Settings) -> AgentDossierEventsService | None:
    try:
        return AgentDossierEventsService(settings)
    except Exception:
        _LOG.debug("Événements dossier indisponibles pour la synthèse", exc_info=True)
        return None


def _build_satellite_situation(
    row: ListeGeneraleRow,
    settings: Settings,
    kind: SatelliteKind,
) -> SituationEnCours:
    join_key = str(row.satellite_join_key() or "").strip()
    sat = _safe_sat_repo(settings)
    events = _safe_events(settings)
    label = _KIND_LABEL.get(kind, kind)
    fields: list[tuple[str, str]] = [("Situation", label)]
    missing = False

    if kind == "mission":
        detail = None
        if sat and join_key:
            try:
                detail = sat.load_missions(legacy_id=join_key).get(join_key)
            except Exception:
                _LOG.debug("Lecture mission satellite", exc_info=True)
        if not _has_mission_detail(detail) and join_key and events:
            dossier = _mission_from_dossier(events.missions(join_key))
            if dossier:
                fields.extend(
                    [
                        ("Nature de la mission", _disp(dossier.nature)),
                        ("Lieu", _disp(dossier.lieu)),
                        ("Réf. NDS", _disp(dossier.reference_dossier)),
                        ("Date de départ", _disp(dossier.date_debut)),
                    ]
                )
                if dossier.periode_sme:
                    fields.append(("Mois SME", _disp(dossier.periode_sme)))
            else:
                missing = True
        elif detail:
            fields.extend(
                [
                    ("Nature de la mission", _disp(detail.nature)),
                    ("Lieu", _disp(detail.lieu_mission)),
                    ("Réf. NDS", _disp(detail.refnds)),
                    ("Date de départ", _disp(detail.date_depart)),
                ]
            )
        else:
            missing = True

    elif kind in ("stage_local", "stage_ex", "stage_interne"):
        detail = None
        if sat and join_key:
            try:
                detail = _stage_loader(sat, kind)(legacy_id=join_key).get(join_key)
            except Exception:
                _LOG.debug("Lecture stage satellite", exc_info=True)
        type_label = label
        if not _has_stage_detail(detail) and join_key and events:
            dossier = _stage_from_dossier(events.stages(join_key), kind)
            if dossier:
                type_label = _STAGE_TYPE_LABEL.get(
                    (dossier.type_stage or "").upper(),
                    dossier.type_stage or label,
                )
                dates = " — ".join(
                    p for p in (_disp(dossier.date_debut), _disp(dossier.date_fin)) if p != "—"
                ) or "—"
                fields[0] = ("Situation", type_label)
                fields.extend(
                    [
                        ("Nature du stage", _disp(dossier.nature)),
                        ("Lieu", _disp(dossier.lieu)),
                        ("Dates", dates),
                    ]
                )
                if (dossier.observation or "").strip():
                    fields.append(("Détail / observation", _disp(dossier.observation)))
                if dossier.periode_sme:
                    fields.append(("Mois SME", _disp(dossier.periode_sme)))
            else:
                missing = True
        elif detail:
            fields.extend(
                [
                    ("Nature du stage", _disp(detail.nature_stage)),
                    ("Lieu", _disp(detail.lieu_stage)),
                    ("Dates", _disp(detail.date_debut_fin)),
                ]
            )
        else:
            missing = True

    elif kind == "malade":
        detail = None
        if sat and join_key:
            try:
                detail = sat.load_malades(legacy_id=join_key).get(join_key)
            except Exception:
                _LOG.debug("Lecture malade satellite", exc_info=True)
        if not _has_malade_detail(detail) and join_key and events:
            malades = events.malades(join_key)
            if malades:
                m = malades[0]
                fields.extend(
                    [
                        ("Date constat", _disp(m.date_debut)),
                        ("Pathologie", _disp(m.pathologie)),
                    ]
                )
                if (m.observation or "").strip():
                    fields.append(("Observation", _disp(m.observation)))
            else:
                missing = True
        elif detail:
            fields.extend(
                [
                    ("Date constat", _disp(detail.date_constat)),
                    ("Pathologie", _disp(detail.pathologie)),
                ]
            )
            if (detail.obs or "").strip():
                fields.append(("Observation", _disp(detail.obs)))
        else:
            missing = True

    if missing:
        fields.append(
            (
                "Détail",
                "Non renseigné en base pour ce mois — vérifier l’import Access "
                "(tables satellites) ou compléter l’onglet « Dossier complet ».",
            )
        )

    return SituationEnCours(fields=fields, missing_satellite_detail=missing)


def _build_detache_situation(row: ListeGeneraleRow, settings: Settings) -> SituationEnCours:
    lookup = _build_sec_lookup(settings)
    sigle = sigle_ou_subdivision(row)
    from sme_gend.models.detaches_file_config import DetachesFileConfig

    cfg: DetachesFileConfig
    try:
        cfg = DetachesConfigService(settings).load()
    except Exception:
        _LOG.debug("Config détachés indisponible", exc_info=True)
        cfg = DetachesFileConfig()
    bloc = designation_bloc_excel(cfg, sigle)
    unite = designation_detachement(row, lookup)

    from sme_gend.services.structure_label_format import format_structure_display
    from sme_gend.services.referentiel_service import ReferentielService

    structures = []
    try:
        structures = ReferentielService(settings).structures()
    except Exception:
        _LOG.debug("Référentiel structures indisponible", exc_info=True)

    struct_orig = format_structure_display(row.structure, structures) or _disp(row.structure)

    fields: list[tuple[str, str]] = [
        ("Situation", "Détaché"),
        ("Sigle (subdivision)", _disp(sigle)),
        ("Unité de détachement", unite),
        ("Structure (liste générale)", struct_orig),
    ]
    if bloc:
        fields.append(("Bloc export détachés", bloc))
    from sme_gend.services.detaches_service import detache_ecart_remarque

    rem = detache_ecart_remarque(row)
    if rem:
        fields.append(("Contrôle", rem))
    return SituationEnCours(fields=fields, missing_satellite_detail=False)
