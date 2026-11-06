"""Share one transaction across an event, its projection and its audit trail."""
from functools import wraps
from inspect import signature
from contextvars import ContextVar

_UNSET = object()
history_audit_context = ContextVar("dossier_history_audit_context", default=None)


def atomic_dossier_write(method):
    contract = signature(method)
    @wraps(method)
    def write(self, *args, **kwargs):
        from sme_gend.services.personnel_service import _personnel_write
        from sme_gend.services.sensitive_data_policy import assert_may_mutate_dossier_history
        expected = kwargs.pop("expected", _UNSET)
        owner = kwargs.pop("owner_legacy_id", None)
        bound = contract.bind(self, *args, **kwargs).arguments
        if method.__name__ == 'add_diplome':
            from sme_gend.services.sensitive_data_policy import assert_may_add_diplome
            assert_may_add_diplome()
        elif method.__name__ in {'add_mission','add_stage','add_malade','add_desertion','add_conge'}:
            from sme_gend.services.sensitive_data_policy import assert_may_add_dated_event
            assert_may_add_dated_event()
        elif hasattr(self, "_assert_history_mutation"):
            self._assert_history_mutation("modifier l'historique du dossier")
        else:
            assert_may_mutate_dossier_history(action="modifier l'historique du dossier")
        if method.__name__.startswith(("update_", "delete_")) and (expected is _UNSET or expected is None):
            raise ValueError("Version de l'historique indisponible. Rechargez le dossier avant de modifier ou supprimer cet événement.")
        with _personnel_write(self._settings, reason="dossier_history_write"):
            context = _guard_history(self, method.__name__, bound, expected, owner)
            from sme_gend.services.business_audit import audit_values
            history_token = history_audit_context.set({
                "before":audit_values(expected) if expected is not _UNSET else None,
                "after":audit_values({k:v for k,v in bound.items() if k not in ("self", "row")}) if not method.__name__.startswith("delete_") else None,
                "history_operation":method.__name__,
            })
            try:
                result = method(self, *args, **kwargs)
            finally:
                history_audit_context.reset(history_token)
            if context is not None:
                family, legacy, agent_id = context
                from sme_gend.services.sensitive_audit_log import _append_entry
                from sme_gend.services.business_audit import audit_values
                operation = method.__name__.split("_", 1)[0]
                identifier = bound.get(family + "_id") or (result if isinstance(result, int) else None)
                _append_entry(action="dossier_" + family + "_" + operation,
                    personnel_id=str(legacy or agent_id), detail=method.__name__,
                    payload={"agent_id":agent_id,"event_id":identifier,
                        "before":audit_values(expected) if expected is not _UNSET else None,
                        "after":audit_values({k:v for k,v in bound.items() if k not in ("self",)}) if operation!="delete" else None})
            return result
    return write


def _guard_history(service, operation, arguments, expected, owner):
    from sme_gend.repositories.write_guard import lock_agent
    settings = service._settings
    family = operation.split("_", 1)[1]
    core = {"diplome": "list_diplomes", "passation": "list_passations",
            "affectation": "list_affectations", "distinction": "list_distinctions"}
    readers = {"mission": "missions", "stage": "stages", "malade": "malades",
               "desertion": "desertions", "conge": "conges"}
    if family in core:
        row = arguments["row"]
        aid = service.agent_id_for_row(row)
        lock_agent(settings, agent_id=aid)
        read = lambda: getattr(service, core[family])(row)
    else:
        legacy = arguments.get("legacy_id")
        if family == "malade" and legacy is None:
            # The old API accepts a child id; UI callers additionally supply
            # the displayed agent, so a wrong child can never cross dossiers.
            aid = lock_agent(settings, event_table="agent_malade", event_id=arguments["malade_id"])
            if expected is _UNSET and owner is None:
                return
            from sme_gend.repositories.mysql_connection import MySQLConnection
            with MySQLConnection(settings).connect(read_only=True) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT legacy_access_id FROM agent WHERE id=%s", (aid,))
                    record = cur.fetchone()
            legacy = str(record[0]) if record else ""
            if owner is not None and str(owner).strip() != legacy:
                raise ValueError("Événement introuvable pour cet agent dans ce dossier.")
        else:
            aid = lock_agent(settings, legacy_id=legacy)
        read = lambda: getattr(service, readers[family])(legacy)
    if expected is not _UNSET:
        event_id = arguments.get(f"{family}_id")
        current = next((entry for entry in read()
                        if getattr(entry, "malade_id" if family == "malade" else "id", None) == event_id), None)
        if current is None or expected is None or type(current) is not type(expected) or current != expected:
            raise ValueError("Cet historique a été modifié ou supprimé depuis son affichage. "
                             "Rechargez le dossier avant de recommencer ; aucune modification enregistrée.")

    return (family, legacy, aid) if family in readers else None
