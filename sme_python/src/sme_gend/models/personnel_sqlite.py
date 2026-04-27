"""Modèle ligne liste générale côté SQLite (migration Access) — PK interne + id_acces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sme_gend.models.liste_generale import ListeGeneraleRow


def _dt_to_sql(v: datetime | None) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="seconds")
    return None


def _sql_to_dt(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    t = str(s).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t[: len(fmt)], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(slots=True)
class PersonnelSqliteRow:
    """
    Ligne `liste_generale` SQLite.

    * ``id`` : clé primaire interne (nouveau système).
    * ``id_acces`` : valeur d'origine ``ListeGenerale.Id`` en Access ; ne doit pas être confondue avec ``id``.
    """

    id: int | None
    id_acces: str | None
    noms: str | None
    date_naissance: datetime | None
    lieu_naissance: str | None
    grade: str | None
    date_grade: datetime | None
    date_service: str | None
    ndecision: str | None
    matricule_militaire: str | None
    matricule_solde: str | None
    position_admin: str | None
    fonction: str | None
    subdivision_arme: str | None
    diplome: str | None
    date_obtention: str | None
    structure: str | None
    obs: str | None
    ordre_tri: int | None
    presseance: int | None

    @classmethod
    def from_liste_generale_access(cls, row: ListeGeneraleRow) -> PersonnelSqliteRow:
        """Construit une ligne SQLite à insérer depuis une ligne Access (Id → id_acces)."""
        id_acc = str(row.id).strip() if row.id else None
        return cls(
            id=None,
            id_acces=id_acc or None,
            noms=row.noms,
            date_naissance=row.date_naissance,
            lieu_naissance=row.lieu_naissance,
            grade=row.grade,
            date_grade=row.date_grade,
            date_service=row.date_service,
            ndecision=row.ndecision,
            matricule_militaire=row.matricule_militaire,
            matricule_solde=row.matricule_solde,
            position_admin=row.position_admin,
            fonction=row.fonction,
            subdivision_arme=row.subdivision_arme,
            diplome=row.diplome,
            date_obtention=row.date_obtention,
            structure=row.structure,
            obs=row.obs,
            ordre_tri=row.ordre_tri,
            presseance=row.presseance,
        )

    def to_liste_generale_row(self) -> ListeGeneraleRow:
        """
        Vue compatible ``ListeGeneraleRow`` pour l'UI / services existants.

        Le champ ``ListeGeneraleRow.id`` reçoit l'identifiant **interne** SQLite (str),
        pas l'Id Access — utiliser ``id_acces`` pour l'ancien identifiant.
        """
        pk = "" if self.id is None else str(self.id)
        return ListeGeneraleRow(
            id=pk,
            access_join_id=self.id_acces,
            noms=self.noms,
            date_naissance=self.date_naissance,
            lieu_naissance=self.lieu_naissance,
            grade=self.grade,
            date_grade=self.date_grade,
            date_service=self.date_service,
            ndecision=self.ndecision,
            matricule_militaire=self.matricule_militaire,
            matricule_solde=self.matricule_solde,
            position_admin=self.position_admin,
            fonction=self.fonction,
            subdivision_arme=self.subdivision_arme,
            diplome=self.diplome,
            date_obtention=self.date_obtention,
            structure=self.structure,
            obs=self.obs,
            ordre_tri=self.ordre_tri,
            presseance=self.presseance,
        )

    @classmethod
    def from_sqlite_sequence(cls, row: tuple[Any, ...]) -> PersonnelSqliteRow:
        """Tuple curseur : colonnes id … presseance (les colonnes created_at / updated_at sont ignorées)."""
        row = row[:20]
        def g(i: int) -> Any:
            return row[i] if i < len(row) else None

        def _int(v: Any) -> int | None:
            if v is None:
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        return cls(
            id=_int(g(0)),
            id_acces=_str_opt(g(1)),
            noms=_str_opt(g(2)),
            date_naissance=_sql_to_dt(_str_opt(g(3))),
            lieu_naissance=_str_opt(g(4)),
            grade=_str_opt(g(5)),
            date_grade=_sql_to_dt(_str_opt(g(6))),
            date_service=_str_opt(g(7)),
            ndecision=_str_opt(g(8)),
            matricule_militaire=_str_opt(g(9)),
            matricule_solde=_str_opt(g(10)),
            position_admin=_str_opt(g(11)),
            fonction=_str_opt(g(12)),
            subdivision_arme=_str_opt(g(13)),
            diplome=_str_opt(g(14)),
            date_obtention=_str_opt(g(15)),
            structure=_str_opt(g(16)),
            obs=_str_opt(g(17)),
            ordre_tri=_int(g(18)),
            presseance=_int(g(19)),
        )

    def to_sql_params_upsert_without_pk(self) -> tuple[Any, ...]:
        """Paramètres pour INSERT (sans id) — 19 colonnes métier + id_acces."""
        return (
            self.id_acces,
            self.noms,
            _dt_to_sql(self.date_naissance),
            self.lieu_naissance,
            self.grade,
            _dt_to_sql(self.date_grade),
            self.date_service,
            self.ndecision,
            self.matricule_militaire,
            self.matricule_solde,
            self.position_admin,
            self.fonction,
            self.subdivision_arme,
            self.diplome,
            self.date_obtention,
            self.structure,
            self.obs,
            self.ordre_tri,
            self.presseance,
        )


def sqlite_update_set_tuple_from_liste_row(row: ListeGeneraleRow) -> tuple[Any, ...]:
    """Valeurs pour ``UPDATE liste_generale SET ... WHERE id = ?`` (SET uniquement)."""
    return (
        row.noms,
        _dt_to_sql(row.date_naissance),
        row.lieu_naissance,
        row.grade,
        _dt_to_sql(row.date_grade),
        row.date_service,
        row.ndecision,
        row.matricule_militaire,
        row.matricule_solde,
        row.position_admin,
        row.fonction,
        row.subdivision_arme,
        row.diplome,
        row.date_obtention,
        row.structure,
        row.obs,
        row.ordre_tri,
        row.presseance,
    )


def sqlite_pk_int_from_row_id(row_id: str) -> int:
    return int(str(row_id).strip())


def _str_opt(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
