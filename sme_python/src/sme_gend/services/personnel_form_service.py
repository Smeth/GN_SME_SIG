"""Persist a completed personnel form and its deferred position together."""
from sme_gend.services.personnel_service import _personnel_write
from sme_gend.services.temporal_events import position_edit
from dataclasses import replace


def save_personnel_form(service, form, *, before=None):
    after = form.result_row()
    with _personnel_write(service.settings, reason="personnel_form_write"), position_edit(service.settings,before) as scope:
        if before is None:
            row_id = service.create_personnel(after)
        else:
            service.update_personnel(before, after)
            row_id = after.id
        # A created career id is not an Access id. Obtain the authoritative
        # saved row before binding any prepared satellite write.
        saved = service.get_by_id(row_id)
        if saved is None:
            raise ValueError("Fiche introuvable après enregistrement : opération annulée.")
        if scope.before is None:
            scope.before=replace(saved,position_admin="Présent")
        form.apply_prepared_writes(saved)
    return service.get_by_id(row_id)
