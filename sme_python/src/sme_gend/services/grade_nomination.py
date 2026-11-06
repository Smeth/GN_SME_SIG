"""Values recorded together when changing an agent's grade."""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GradeNomination:
    date_nomination: datetime
    reference: str | None = None
