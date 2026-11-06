"""Application-created identities use the existing GEN namespace, not an Access number."""
from uuid import uuid4


def new_internal_personnel_key() -> str:
    # GEN- is already recognized by import reconciliation. UUIDs do not depend
    # on names, dates, career ids, workstation clocks or the imported month.
    # The filename-safe spelling is also suitable for shared photo folders.
    return "GEN-APP-" + uuid4().hex



def stage_source_access_id(key: str) -> str | None:
    """The optional 32-character provenance is an external Access id.

    Internal identities are linked by agent_id, not truncated into this legacy
    field. NULL is already supported by stage loaders and monthly mutations.
    """
    return None if str(key).startswith("GEN-") else key
