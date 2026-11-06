"""Synchronisation dossier agent après saisie manuelle (liste générale).

Règle SoT (P0.1) :
- ``agent_diplome``, ``agent_passation_grade``, ``agent_affectation`` = maîtres ;
- ``carriere_situation`` (grade / diplôme / structure affichés) = projection ;
- toute saisie liste met à jour le dossier puis appelle ``sync_carriere_from_dossier``.
"""

from __future__ import annotations

from datetime import date, datetime
from contextlib import contextmanager
from contextvars import ContextVar

_affectation_note = ContextVar('affectation_note', default=None)


@contextmanager
def affectation_note_scope(reference):
    token = _affectation_note.set((reference or '').strip() or None)
    try:
        yield
    finally:
        _affectation_note.reset(token)

from sme_gend.config.settings import Settings
from sme_gend.config.sme_context import mois_actif_courant
from sme_gend.models.agent_dossier import DIPLOME_DEFAULT
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.mysql_agent_dossier_repository import MySQLAgentDossierRepository
from sme_gend.services.access_date_parse import parse_access_date
from sme_gend.services.audit_log import diff_personnel_rows
from sme_gend.services.mysql_import_period import first_day_of_month

_DOSSIER_FIELDS = frozenset(
    {
        "diplome",
        "date_obtention",
        "grade",
        "date_grade",
        "ndecision",
        "structure",
        "fonction",
        "subdivision_arme",
    }
)


def _norm(s: str | None) -> str | None:
    t = (s or "").strip()
    return t or None


def _active_mois() -> str:
    ym = (mois_actif_courant() or "").strip()
    if ym:
        return ym
    return date.today().strftime("%Y-%m")


def _grade_date_from_row(row: ListeGeneraleRow, mois: str) -> date:
    dg = row.date_grade
    if isinstance(dg, datetime):
        return dg.date()
    if isinstance(dg, date):
        return dg
    return first_day_of_month(mois)


def _diplome_date_from_row(row: ListeGeneraleRow) -> date | None:
    return parse_access_date(row.date_obtention)


def validate_monthly_history_edit(before, after):
    if _norm(before.grade) and not _norm(after.grade):
        raise ValueError(
            "Pour retirer un grade, ouvrez la fiche complète, rubrique Passations de grade. "
            "Vider la liste mensuelle ne supprime pas l'historique ; aucune modification enregistrée."
        )
    for field, label in (("diplome", "le diplôme"), ("date_obtention", "la date d'obtention du diplôme")):
        if getattr(before, field, None) and not getattr(after, field, None):
            raise ValueError(
                f"Pour retirer ou corriger {label}, ouvrez le dossier agent, rubrique Diplômes. "
                "Vider la liste mensuelle ne supprime pas un événement historique ; aucune modification enregistrée."
            )


def sync_dossier_after_personnel_save(
    settings: Settings,
    before: ListeGeneraleRow,
    after: ListeGeneraleRow,
) -> None:
    """
    Alimente le dossier depuis la liste puis projette vers ``carriere_situation``.

    Les champs identité / padmin / obs restent écrits directement en carrière
    par ``PersonnelService.update_personnel`` (hors périmètre dossier).
    """
    changes = diff_personnel_rows(before, after)
    if not changes:
        return

    repo = MySQLAgentDossierRepository(settings)
    agent_id = repo.resolve_agent_id(
        carriere_id=after.id,
        legacy_id=after.satellite_join_key(),
    )

    from sme_gend.services.sme_month_closure import canonical_sme_mois_yyyy_mm
    ym = canonical_sme_mois_yyyy_mm(getattr(settings, "mysql_view_mois", None) or _active_mois())
    if not ym:
        raise ValueError("Une période SME valide est nécessaire pour modifier le dossier.")
    obs = "Saisie applicative"
    if _affectation_note.get():
        obs += '\nRéf. note : ' + _affectation_note.get()
    dossier_touched = False

    if ("diplome" in changes or "date_obtention" in changes) and _norm(after.diplome):
        repo.record_list_saisie_diplome(
            agent_id,
            libelle=after.diplome or "",
            date_obtention=_diplome_date_from_row(after),
            categorie=DIPLOME_DEFAULT,
            obs=obs,
            previous_date=_diplome_date_from_row(before) if _norm(before.diplome)==_norm(after.diplome) else None,
        )
        dossier_touched = True

    if ("grade" in changes or "date_grade" in changes or "ndecision" in changes) and _norm(
        after.grade
    ):
        repo.record_list_saisie_passation(
            agent_id,
            grade_libelle=after.grade or "",
            date_passation=_grade_date_from_row(after, ym),
            ndecision=_norm(after.ndecision),
            obs=obs,
        )
        dossier_touched = True

    if "structure" in changes and _norm(after.structure):
        repo.record_list_saisie_affectation(
            agent_id,
            structure_code=after.structure or "",
            date_affectation=first_day_of_month(ym),
            fonction=_norm(after.fonction),
            subdivision_arme=_norm(after.subdivision_arme),
            sme_periode_mois=ym,
            obs=obs,
        )
        dossier_touched = True
    elif (
        ("fonction" in changes or "subdivision_arme" in changes)
        and _norm(after.structure)
    ):
        repo.record_list_saisie_affectation(
            agent_id,
            structure_code=after.structure or "",
            date_affectation=first_day_of_month(ym),
            fonction=_norm(after.fonction),
            subdivision_arme=_norm(after.subdivision_arme),
            sme_periode_mois=ym,
            obs=obs,
        )
        dossier_touched = True

    carriere_id = str(after.id or "").strip()
    if not carriere_id:
        return

    if dossier_touched:
        components = set()
        if {'diplome', 'date_obtention'}.intersection(changes):
            dates=tuple(d for d in (_diplome_date_from_row(before),_diplome_date_from_row(after)) if d)
            if dates:
                repo.apply_dated_grade(agent_id,before,min(dates),carriere_id=carriere_id,component='diplome',protected_dates=dates)
            else:
                components.add('diplome')
        if {'grade', 'date_grade', 'ndecision'}.intersection(changes):
            repo.apply_dated_grade(agent_id, before, _grade_date_from_row(after, ym), carriere_id=carriere_id)
        if {'structure', 'fonction', 'subdivision_arme'}.intersection(changes):
            components.add('affectation')
        if components:
            repo.sync_carriere_from_dossier(agent_id, carriere_id, components=frozenset(components))
        if 'affectation' in components:
            repo.assert_carriere_affectation(carriere_id, after, changes.keys())
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(settings)

    if before.position_admin != after.position_admin:
        from sme_gend.services.temporal_events import plain_position_change
        plain_position_change(settings,before,after)
