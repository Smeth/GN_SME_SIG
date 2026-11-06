"""Displayed rule versions; no campaign schema or user data migrations."""
from dataclasses import dataclass, field, replace
import re

from sme_gend.models.avancement import AvancementRegle
from sme_gend.repositories.mysql_connection import mysql_database_key
from sme_gend.repositories.reference_collection_guard import fingerprint
from sme_gend.repositories.reference_write_guard import ReferenceConflictError

RULE_COLUMNS = (
    "grade_cible", "grade_precedent", "libelle", "annees_grade",
    "annees_grade_ecole", "annees_services", "age_min", "age_max", "diplomes",
    "diplome_ou_superieur", "inscription_tableau", "voie", "acte",
    "non_codifie", "condition_manuelle",
)
RULE_SELECT = "SELECT " + ",".join(RULE_COLUMNS) + " FROM avancement_regle"


@dataclass(frozen=True, slots=True)
class AvancementRulesSnapshot:
    rows: tuple[AvancementRegle, ...]
    revision: str = field(repr=False)
    database: tuple = field(repr=False)


def bind_rule(settings, rule, raw):
    return replace(rule, write_token=fingerprint(tuple(raw) if raw is not None else None),
        _write_database=mysql_database_key(settings))


def load_rules(cursor, settings, *, locking=False):
    cursor.execute(RULE_SELECT + " ORDER BY grade_cible" + (" FOR UPDATE" if locking else ""))
    raw = tuple(tuple(row) for row in cursor.fetchall())
    return AvancementRulesSnapshot(
        tuple(bind_rule(settings, AvancementRegle.from_db(*row), row) for row in raw),
        fingerprint(raw), mysql_database_key(settings),
    )


def assert_rule_permission():
    from sme_gend.services.auth_guard import session_has_permission
    if not session_has_permission("avancement.regles"):
        raise PermissionError("Permission avancement.regles requise pour modifier le barème.")


def require_rule(settings, proposed, expected):
    if (not isinstance(proposed, AvancementRegle) or not isinstance(expected, AvancementRegle)
        or proposed.grade_cible != expected.grade_cible
        or expected._write_database != mysql_database_key(settings)
        or not isinstance(expected.write_token, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected.write_token) is None):
        raise ValueError("Version de règle indisponible. Rechargez le barème avant d'enregistrer.")


def check_rule(cursor, expected):
    cursor.execute(RULE_SELECT + " WHERE grade_cible=%s FOR UPDATE", (expected.grade_cible,))
    row = cursor.fetchone()
    if fingerprint(tuple(row) if row is not None else None) != expected.write_token:
        raise ReferenceConflictError("Cette règle a été modifiée ou supprimée depuis son affichage. "
            "Votre modification n'a pas été enregistrée ; rechargez le barème.")


def require_rules_snapshot(settings, expected):
    if (not isinstance(expected, AvancementRulesSnapshot)
        or expected.database != mysql_database_key(settings)
        or not isinstance(expected.revision, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected.revision) is None):
        raise ValueError("Version du barème indisponible. Rechargez les règles avant de les rétablir.")
