"""Application des fiches satellites après changement de position admin."""

from __future__ import annotations

from typing import Callable
from sme_gend.services.temporal_events import position_edit
from sme_gend.repositories.monthly_write_guard import UNSET

DeferredWrite = Callable[..., None]

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from sme_gend.config.settings import Settings
from sme_gend.models.liste_generale import ListeGeneraleRow
from sme_gend.repositories.numerique_satellite_repository import NumeriqueSatelliteRepository
from sme_gend.services.padmin_satellite import (
    SatelliteKind,
    kind_requires_satellite_prompt,
    padmin_label_for_kind,

)
from sme_gend.ui.padmin_satellite_dialog import (
    BulkMissionStageDialog,
    PadminSatelliteDialog,
    SatelliteFormResult,
)


def _load_existing(
    sat: NumeriqueSatelliteRepository,
    kind: SatelliteKind,
    join_key: str,
):
    if kind == "malade":
        return sat.load_malades().get(join_key)
    if kind == "mission":
        return sat.load_missions().get(join_key)
    if kind == "stage_local":
        return sat.load_stages_loc().get(join_key)
    if kind == "stage_interne":
        return sat.load_stages_internal().get(join_key)
    return sat.load_stages_ex().get(join_key)


def _apply_form(sat: NumeriqueSatelliteRepository, join_key: str, form: SatelliteFormResult, *, expected=UNSET) -> None:
    options = {} if expected is UNSET else {"expected": expected}
    if form.kind == "malade" and form.malade:
        sat.upsert_malade(join_key, form.malade, **options)
    elif form.kind == "mission" and form.mission:
        sat.upsert_mission(join_key, form.mission, **options)
    elif form.stage and form.kind in ("stage_local", "stage_ex", "stage_interne"):
        sat.upsert_stage(
            join_key,
            form.stage,
            type_code={
                "stage_local": "LOCAL",
                "stage_ex": "EXTERIEUR",
                "stage_interne": "INTERNE",
            }[form.kind],
            date_debut=form.stage_date_debut,
            date_fin=form.stage_date_fin,
            **options,
        )


def prompt_satellite_for_row(
    parent: QWidget,
    settings: Settings,
    row: ListeGeneraleRow,
    *,
    old_padmin: str | None,
    new_padmin: str | None,
    prepare_only: bool = False,
) -> bool | DeferredWrite:
    kind = kind_requires_satellite_prompt(old_padmin, new_padmin)
    if kind is None:
        return True
    join_key = row.satellite_join_key()
    if not join_key:
        return False
    sat = NumeriqueSatelliteRepository(settings)
    existing = _load_existing(sat, kind, join_key)
    kw: dict = {}
    if kind == "malade":
        kw["malade"] = existing
    elif kind == "mission":
        kw["mission"] = existing
    else:
        kw["stage"] = existing
    label = row.noms or join_key
    form = PadminSatelliteDialog.run(parent, kind=kind, agent_label=label, **kw)
    if form is None:
        return False
    def write_satellite(target_row=None):
        target_key = target_row.satellite_join_key() if target_row is not None else join_key
        snapshot = existing if target_key == join_key else None
        _apply_form(sat, target_key, form, expected=snapshot)
    if prepare_only:
        return write_satellite
    try:
        write_satellite()
    except Exception as exc:
        QMessageBox.warning(
            parent,
            "Fiche complémentaire",
            f"Enregistrement de la fiche impossible :\n{exc}",
        )
        return False
    return True


def prompt_admin_conge_for_row(
    parent: QWidget, settings: Settings, row: ListeGeneraleRow,
    *, type_code: str = "ADMIN", prepare_only: bool = False,
) -> bool | DeferredWrite:
    """Enregistre le congé dans le registre consommé par le parcours du dossier."""
    from sme_gend.config.liste_generale_scope import build_export_mois_context
    from sme_gend.services.agent_conge_service import CONGE_TYPE_LABELS
    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.ui.conge_edit_dialog import CongeEditDialog

    try:
        key = row.satellite_join_key()
        if not key:
            raise ValueError("Identifiant de liaison du dossier manquant.")
        dlg = CongeEditDialog(
            parent, settings, key,
            sme_periode_mois=build_export_mois_context(settings).export_mois,
        )
        dlg.setWindowTitle(f"{CONGE_TYPE_LABELS[type_code]} — {row.noms or key}")
        dlg._type.setCurrentIndex(dlg._type.findData(type_code))
        dlg._type.setEnabled(False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        values = dict(dlg.values())
        def write_conge(target_row=None):
            target_key = target_row.satellite_join_key() if target_row is not None else key
            AgentDossierEventsService(settings).add_conge(target_key, **values)
        if prepare_only:
            return write_conge
        write_conge()
    except Exception as exc:
        QMessageBox.warning(
            parent, CONGE_TYPE_LABELS.get(type_code, "Congé"),
            f"Enregistrement du congé dans le dossier impossible :\n{exc}",
        )
        return False
    return True


def additional_position_kind(position_admin: str | None) -> str | None:
    from sme_gend.services.agent_conge_service import padmin_to_conge_type
    from sme_gend.services.position_admin_events import POSITION_LABELS

    conge_type = padmin_to_conge_type(position_admin)
    if conge_type in ("ADMIN", "EXPECTATIVE"):
        return conge_type
    normalized = " ".join((position_admin or "").casefold().split())
    if normalized == "déserteur":
        return "Déserteur"
    return POSITION_LABELS.get(normalized)


def prompt_additional_position_for_row(parent, settings, row, *, kind, prepare_only=False):
    if kind in ("ADMIN", "EXPECTATIVE"):
        options = {"prepare_only": True} if prepare_only else {}
        if kind == "ADMIN":
            return prompt_admin_conge_for_row(parent, settings, row, **options)
        return prompt_admin_conge_for_row(parent, settings, row, type_code=kind, **options)

    from sme_gend.services.agent_dossier_events_service import AgentDossierEventsService
    from sme_gend.services.position_admin_events import add_position_event
    from sme_gend.config.liste_generale_scope import build_export_mois_context
    from sme_gend.ui.agent_desertion_edit_dialog import AgentDesertionEditDialog
    from sme_gend.ui.position_admin_event_dialog import PositionAdminEventDialog

    try:
        key = row.satellite_join_key()
        if not key:
            raise ValueError("Identifiant de liaison du dossier manquant.")
        if kind == "Déserteur":
            dlg = AgentDesertionEditDialog(parent)
            dlg.setWindowTitle(f"Désertion — {row.noms or key}")
        else:
            dlg = PositionAdminEventDialog(parent, position_admin=kind, agent_label=row.noms or key)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        values = dict(dlg.values())
        if kind == "Déserteur":
            def write_position(target_row=None):
                target_key = target_row.satellite_join_key() if target_row is not None else key
                AgentDossierEventsService(settings).add_desertion(target_key, **values)
        else:
            mois = build_export_mois_context(settings).export_mois
            def write_position(target_row=None):
                target_key = target_row.satellite_join_key() if target_row is not None else key
                add_position_event(settings, target_key, position_admin=kind, mois_sme=mois, **values)
        if prepare_only:
            return write_position
        write_position()
    except Exception as exc:
        QMessageBox.warning(parent, kind, f"Enregistrement dans le dossier impossible :\n{exc}")
        return False
    return True


def prepare_position_change(parent, settings, row, *, old_padmin, new_padmin):
    """Collect the form first; the caller owns the eventual write transaction."""
    kind = additional_position_kind(new_padmin)
    if kind is not None:
        if kind == additional_position_kind(old_padmin):
            return True
        return prompt_additional_position_for_row(parent, settings, row, kind=kind, prepare_only=True)
    return prompt_satellite_for_row(parent, settings, row, old_padmin=old_padmin,
                                    new_padmin=new_padmin, prepare_only=True)


def _bulk_stage_nature_suggestions(settings: Settings) -> list[str]:
    from sme_gend.services.stages_list_filter import load_known_stage_natures

    try:
        return load_known_stage_natures(settings)
    except Exception:
        return []


def _load_bulk_snapshots(sat, kinds):
    loaders = {
        "mission": sat.load_missions, "malade": sat.load_malades,
        "stage_local": sat.load_stages_loc, "stage_ex": sat.load_stages_ex,
        "stage_interne": sat.load_stages_internal,
    }
    return {kind: loaders[kind]() for kind in kinds}


def bulk_apply_mission_or_stage(
    parent: QWidget,
    settings: Settings,
    rows: list[ListeGeneraleRow],
) -> tuple[int, list[str]] | None:
    """
    Dialogue masse → mise à jour padmin + fiche satellite pour chaque ligne.
    Retourne ``None`` si l'utilisateur annule le dialogue.
    """
    if not rows:
        return 0, []
    sat = NumeriqueSatelliteRepository(settings)
    try:
        snapshots = _load_bulk_snapshots(sat, ("mission", "stage_local", "stage_ex", "stage_interne"))
    except Exception as exc:
        return 0, [f"Lecture préalable impossible : {exc}"]
    dlg = BulkMissionStageDialog.run(
        parent,
        count=len(rows),
        nature_suggestions=_bulk_stage_nature_suggestions(settings),
    )
    if dlg is None:
        return None
    kind, form = dlg
    new_padmin = padmin_label_for_kind(kind)
    from sme_gend.services.personnel_service import PersonnelService

    personnel = PersonnelService(settings)
    ok = 0
    errs: list[str] = []
    from dataclasses import replace

    for row in rows:
        join_key = row.satellite_join_key()
        if not join_key:
            errs.append(f"{row.noms or row.id}: identifiant liaison vide")
            continue
        updated = replace(row, position_admin=new_padmin)
        try:
            from sme_gend.services.personnel_service import _personnel_write
            with _personnel_write(settings), position_edit(settings,row):
                personnel.update_personnel(
                    row, updated, edit_motif="Affectation groupée de position administrative.",
                )
                _apply_form(sat, join_key, form, expected=snapshots[kind].get(join_key))
            ok += 1
        except Exception as exc:
            if getattr(exc, "committed", False):
                ok += 1
                errs.append(f"{row.noms or row.id}: {exc}")
            else:
                errs.append(f"{row.noms or row.id}: enregistrement annulé : {exc}")
    if ok:
        from sme_gend.services.liste_generale_cache import invalidate_liste_cache

        invalidate_liste_cache(settings)
    return ok, errs


def prompt_satellite_bulk_shared(
    parent: QWidget,
    settings: Settings,
    rows: list[ListeGeneraleRow],
    *,
    new_padmin: str,
    prepare_only: bool = False,
):
    """Collect a shared form, optionally deferring writes to the caller transaction."""
    from sme_gend.services.padmin_satellite import satellite_kind

    kind = satellite_kind(new_padmin)
    if kind is None or not rows:
        return True if prepare_only else None
    sat = NumeriqueSatelliteRepository(settings)
    try:
        snapshots = _load_bulk_snapshots(sat, (kind,))[kind]
    except Exception as exc:
        QMessageBox.warning(parent, "Fiches complémentaires", f"Lecture préalable impossible : {exc}")
        return False if prepare_only else None
    form = PadminSatelliteDialog.run(
        parent,
        kind=kind,
        agent_label=f"{len(rows)} agent(s)",
    )
    if form is None:
        return False if prepare_only else None
    if prepare_only:
        return lambda row: _apply_form(sat, row.satellite_join_key(), form, expected=snapshots.get(row.satellite_join_key()))
    errs: list[str] = []
    for row in rows:
        key = row.satellite_join_key()
        if not key:
            errs.append(row.noms or row.id or "?")
            continue
        try:
            from sme_gend.services.personnel_service import _personnel_write
            with _personnel_write(settings):
                _apply_form(sat, key, form, expected=snapshots.get(key))
        except Exception as exc:
            errs.append(f"{row.noms or row.id}: {exc}")
    if errs:
        QMessageBox.warning(
            parent,
            "Fiches complémentaires",
            "Certaines fiches n'ont pas pu être enregistrées :\n" + "\n".join(errs[:10]),
        )
