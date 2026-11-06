"""Bounded, explicit consultation events; never record keystrokes or search text."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Consultation:
    action: str
    actor: str
    month: str | None
    section: str
    personnel_id: str | None = None
    result_count: int | None = None


def capture_consultation(settings, *, action, section, row=None, result_count=None):
    if action not in {"section_consulted", "dossier_tab_consulted", "search_results_consulted"}:
        raise ValueError("Type de consultation inconnu.")
    from sme_gend.services.auth_context import capture_audit_identity
    import getpass
    actor = capture_audit_identity() or ('Windows:' + getpass.getuser())
    # The UI freezes only in-memory context. No central config read on its thread.
    return Consultation(action, actor, settings.mysql_view_mois or None,
                        str(section)[:160], str(row.id) if row is not None else None,
                        max(0, int(result_count)) if result_count is not None else None)


def append_consultation(settings, event):
    from sme_gend.repositories.app_config_repository import AppConfigRepository
    payload = {"section":event.section, "detail":"Consultation : " + event.section}
    if event.result_count is not None:
        payload["result_count"] = event.result_count
    AppConfigRepository(settings).append_audit(action=event.action, module="consultation",
        actor=event.actor, sme_mois_context=event.month, personnel_id=event.personnel_id, payload=payload)


def queue_consultation(parent, settings, *, action, section, row=None, result_count=None, on_error=None):
    from sme_gend.ui.bg_task import run_in_background
    event = capture_consultation(settings, action=action, section=section, row=row, result_count=result_count)
    return run_in_background(parent, lambda: append_consultation(settings, event), lambda _: None,
                             on_error=on_error)
