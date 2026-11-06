"""Prépare les lectures d'une fiche de consultation hors du thread graphique."""
from sme_gend.repositories.mysql_agent_repository import MySQLAgentRepository
from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
from sme_gend.services.agent_dossier_service import AgentDossierService
from sme_gend.services.personnel_dossier_history import build_audit_timeline
from sme_gend.services.personnel_situation_context import build_situation_en_cours
from sme_gend.services.position_admin_events import list_position_events
from sme_gend.services.referentiel_service import ReferentielService
from sme_gend.services.career_alerts_cache import dossier_alerts_for_agent as alerts_for_agent
from sme_gend.services.personnel_media import person_photo_path, grade_insignia_path, personnel_media_root


def load_dossier_read_snapshot(settings, row, *, include_alerts=True):
    leg = str(row.satellite_join_key() or "").strip()
    events = AgentDossierEventsService(settings)
    dossier = AgentDossierService(settings)
    references = ReferentielService(settings)
    grades = references.grades()
    photo = person_photo_path(leg or row.id)
    if photo is None:
        from sme_gend.services.agent_identity_alias import aliases_for
        for old in aliases_for(settings, leg):
            photo = person_photo_path(old)
            if photo is not None:
                break
    return {
        "career_snapshots": MySQLAgentRepository(settings).list_carriere_history_for_agent(leg),
        "events": {name: list(getattr(events, name)(leg))
                   for name in ("missions", "stages", "malades", "desertions", "conges")},
        "dossier": dossier.read_bundle(row),
        "grades": grades, "structures": references.structures(),
        "media": {"root": personnel_media_root(), "photo": photo,
                  "grade": grade_insignia_path(row.grade, grades)},
        "positions": list_position_events(settings, leg),
        "timeline": build_audit_timeline(leg, settings=settings) if leg else [],
        "situation": build_situation_en_cours(row, settings),
        "alerts": alerts_for_agent(settings, leg) if include_alerts else [],
        "alerts_pending": not include_alerts,
    }
